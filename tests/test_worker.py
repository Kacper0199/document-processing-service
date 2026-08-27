from datetime import UTC, datetime

import pytest

from papertrail.domain import JobFailure, JobSubmission, ProcessingResult
from papertrail.repository import InMemoryJobRepository
from papertrail.worker import InMemoryWorkQueue, ProcessingError, WorkerSupervisor


@pytest.mark.asyncio
async def test_retries_then_completes_job():
    repository = InMemoryJobRepository()
    queue = InMemoryWorkQueue()
    created = await repository.create_or_reuse(
        JobSubmission(document_url="https://example.org/report.pdf", operation="extract_markdown"),
        "retry-test",
    )
    calls = 0

    async def handle(job):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProcessingError(JobFailure("timeout", "Temporary timeout", True))
        return ProcessingResult("test", "1", {}, {"finished": datetime.now(UTC).isoformat()})

    worker = WorkerSupervisor(repository, queue, handle, retry_delay_seconds=0)
    await worker.start()
    await queue.enqueue(created.job.id)

    for _ in range(50):
        job = await repository.get(created.job.id)
        if job and job.state.value == "succeeded":
            break
        await __import__("asyncio").sleep(0.01)

    await worker.stop()
    assert job.state.value == "succeeded"
    assert job.attempt_count == 2
