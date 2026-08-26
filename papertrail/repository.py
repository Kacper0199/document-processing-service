import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from papertrail.domain import Job, JobCreateResult, JobFailure, JobState, JobSubmission, ProcessingResult


class IdempotencyConflictError(Exception):
    pass


class InMemoryJobRepository:
    def __init__(self) -> None:
        self._jobs: dict[UUID, Job] = {}
        self._idempotency_index: dict[str, UUID] = {}
        self._lock = asyncio.Lock()

    async def create_or_reuse(
        self,
        submission: JobSubmission,
        idempotency_key: str,
    ) -> JobCreateResult:
        key_hash = self._digest(idempotency_key)
        fingerprint = self._fingerprint(submission)
        async with self._lock:
            existing_id = self._idempotency_index.get(key_hash)
            if existing_id is not None:
                existing = self._jobs[existing_id]
                if existing.request_fingerprint != fingerprint:
                    raise IdempotencyConflictError
                return JobCreateResult(job=existing, reused=True)
            now = datetime.now(UTC)
            job = Job.queued(
                idempotency_key_hash=key_hash,
                request_fingerprint=fingerprint,
                document_url=str(submission.document_url),
                operation=submission.operation,
                now=now,
            )
            self._jobs[job.id] = job
            self._idempotency_index[key_hash] = job.id
            return JobCreateResult(job=job, reused=False)

    async def get(self, job_id: UUID) -> Job | None:
        async with self._lock:
            return self._jobs.get(job_id)

    async def claim(self, job_id: UUID) -> Job | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state is not JobState.QUEUED:
                return None
            now = datetime.now(UTC)
            claimed = replace(
                job,
                state=JobState.RUNNING,
                attempt_count=job.attempt_count + 1,
                started_at=now,
                updated_at=now,
                failure=None,
            )
            self._jobs[job_id] = claimed
            return claimed

    async def reschedule(self, job_id: UUID, failure: JobFailure) -> Job:
        return await self._transition(job_id, JobState.QUEUED, failure=failure)

    async def succeed(self, job_id: UUID, result: ProcessingResult) -> Job:
        return await self._transition(job_id, JobState.SUCCEEDED, result=result)

    async def fail(self, job_id: UUID, failure: JobFailure) -> Job:
        return await self._transition(job_id, JobState.FAILED, failure=failure)

    async def _transition(
        self,
        job_id: UUID,
        state: JobState,
        *,
        result: ProcessingResult | None = None,
        failure: JobFailure | None = None,
    ) -> Job:
        async with self._lock:
            job = self._jobs[job_id]
            now = datetime.now(UTC)
            updated = replace(
                job,
                state=state,
                updated_at=now,
                finished_at=now if state in {JobState.SUCCEEDED, JobState.FAILED} else None,
                result=result,
                failure=failure,
            )
            self._jobs[job_id] = updated
            return updated

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _fingerprint(submission: JobSubmission) -> str:
        payload = json.dumps(submission.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()
