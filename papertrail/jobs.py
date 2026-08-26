from uuid import UUID

from papertrail.domain import Job, JobCreateResult, JobSubmission
from papertrail.ports import JobRepository


class JobService:
    def __init__(self, repository: JobRepository) -> None:
        self._repository = repository

    async def submit(self, submission: JobSubmission, idempotency_key: str) -> JobCreateResult:
        return await self._repository.create_or_reuse(submission, idempotency_key)

    async def get(self, job_id: UUID) -> Job | None:
        return await self._repository.get(job_id)
