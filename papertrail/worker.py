import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

from papertrail.domain import Job, JobFailure, ProcessingResult
from papertrail.ports import JobRepository, WorkQueue

ProcessingHandler = Callable[[Job], Awaitable[ProcessingResult]]


class ProcessingError(Exception):
    def __init__(self, failure: JobFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


class InMemoryWorkQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[UUID] = asyncio.Queue()

    async def enqueue(self, job_id: UUID) -> None:
        await self._queue.put(job_id)

    async def dequeue(self) -> UUID:
        return await self._queue.get()

    def depth(self) -> int:
        return self._queue.qsize()


class WorkerSupervisor:
    def __init__(
        self,
        repository: JobRepository,
        queue: WorkQueue,
        handler: ProcessingHandler,
        *,
        retry_limit: int = 3,
        retry_delay_seconds: float = 1,
    ) -> None:
        self._repository = repository
        self._queue = queue
        self._handler = handler
        self._retry_limit = retry_limit
        self._retry_delay_seconds = retry_delay_seconds
        self._worker_task: asyncio.Task[None] | None = None
        self._retry_tasks: set[asyncio.Task[None]] = set()
        self._started = asyncio.Event()

    @property
    def is_ready(self) -> bool:
        return self._started.is_set() and self._worker_task is not None

    async def start(self) -> None:
        if self._worker_task is not None:
            return
        self._worker_task = asyncio.create_task(self._run(), name="papertrail-worker")
        self._started.set()

    async def stop(self) -> None:
        tasks = [task for task in [self._worker_task, *self._retry_tasks] if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._worker_task = None
        self._retry_tasks.clear()
        self._started.clear()

    async def _run(self) -> None:
        while True:
            job_id = await self._queue.dequeue()
            job = await self._repository.claim(job_id)
            if job is None:
                continue
            await self._process(job)

    async def _process(self, job: Job) -> None:
        try:
            result = await self._handler(job)
        except ProcessingError as error:
            await self._handle_failure(job, error.failure)
        except Exception:
            await self._handle_failure(
                job,
                JobFailure(
                    code="processor_error",
                    message="The document processor failed unexpectedly.",
                    retryable=True,
                ),
            )
        else:
            await self._repository.succeed(job.id, result)

    async def _handle_failure(self, job: Job, failure: JobFailure) -> None:
        if failure.retryable and job.attempt_count < self._retry_limit:
            await self._repository.reschedule(job.id, failure)
            retry_task = asyncio.create_task(
                self._enqueue_after_delay(job.id, self._retry_delay(job.attempt_count)),
                name=f"papertrail-retry-{job.id}",
            )
            self._retry_tasks.add(retry_task)
            retry_task.add_done_callback(self._retry_tasks.discard)
            return
        await self._repository.fail(job.id, failure)

    async def _enqueue_after_delay(self, job_id: UUID, delay_seconds: float) -> None:
        await asyncio.sleep(delay_seconds)
        await self._queue.enqueue(job_id)

    def _retry_delay(self, attempt_count: int) -> float:
        return self._retry_delay_seconds * (2 ** (attempt_count - 1))
