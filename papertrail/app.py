from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, status

from papertrail.domain import Job, JobAccepted, JobStatus, JobSubmission
from papertrail.jobs import JobService
from papertrail.repository import IdempotencyConflictError, InMemoryJobRepository


def create_app(service: JobService | None = None) -> FastAPI:
    app = FastAPI(title="Papertrail", version="0.1.0")
    job_service = service or JobService(InMemoryJobRepository())

    @app.post("/jobs", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
    async def create_job(
        submission: JobSubmission,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> JobAccepted:
        _validate_idempotency_key(idempotency_key)
        try:
            result = await job_service.submit(submission, idempotency_key)
        except IdempotencyConflictError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="idempotency_key_conflict",
            ) from error
        return JobAccepted(job_id=result.job.id, state=result.job.state, reused=result.reused)

    @app.get("/jobs/{job_id}", response_model=JobStatus)
    async def get_job(job_id: UUID) -> JobStatus:
        job = await job_service.get(job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job_not_found")
        return _to_status(job)

    return app


def _validate_idempotency_key(value: str) -> None:
    if not 1 <= len(value) <= 128 or any(ord(character) < 33 or ord(character) > 126 for character in value):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_idempotency_key")


def _to_status(job: Job) -> JobStatus:
    return JobStatus(
        id=job.id,
        state=job.state,
        operation=job.operation,
        attempt_count=job.attempt_count,
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        input_lineage=job.input_lineage,
        result=job.result,
        failure=job.failure,
    )
