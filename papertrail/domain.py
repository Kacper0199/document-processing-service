from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import AnyHttpUrl, BaseModel, Field


class Operation(StrEnum):
    EXTRACT_MARKDOWN = "extract_markdown"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobSubmission(BaseModel):
    document_url: AnyHttpUrl
    operation: Operation


@dataclass(frozen=True, slots=True)
class InputLineage:
    document_url: str
    sha256: str
    content_type: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    processor: str
    processor_version: str
    artifacts: dict[str, str]
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class JobFailure:
    code: str
    message: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class Job:
    id: UUID
    idempotency_key_hash: str
    request_fingerprint: str
    document_url: str
    operation: Operation
    state: JobState
    attempt_count: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    input_lineage: InputLineage | None = None
    result: ProcessingResult | None = None
    failure: JobFailure | None = None

    @classmethod
    def queued(
        cls,
        *,
        idempotency_key_hash: str,
        request_fingerprint: str,
        document_url: str,
        operation: Operation,
        now: datetime,
    ) -> "Job":
        return cls(
            id=uuid4(),
            idempotency_key_hash=idempotency_key_hash,
            request_fingerprint=request_fingerprint,
            document_url=document_url,
            operation=operation,
            state=JobState.QUEUED,
            attempt_count=0,
            created_at=now,
            updated_at=now,
        )


@dataclass(frozen=True, slots=True)
class JobCreateResult:
    job: Job
    reused: bool


class JobAccepted(BaseModel):
    job_id: UUID
    state: JobState
    reused: bool


class JobStatus(BaseModel):
    id: UUID
    state: JobState
    operation: Operation
    attempt_count: int = Field(ge=0, le=3)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    input_lineage: InputLineage | None
    result: ProcessingResult | None
    failure: JobFailure | None
