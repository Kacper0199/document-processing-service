from papertrail.ports import JobRepository, WorkQueue


class JobService:
    def __init__(self, repository, queue=None):
        self._repository = repository
        self._queue = queue

    async def submit(self, submission, idempotency_key):
        result = await self._repository.create_or_reuse(submission, idempotency_key)
        if not result.reused and self._queue:
            await self._queue.enqueue(result.job.id)
        return result

    async def get(self, job_id):
        return await self._repository.get(job_id)
