from pathlib import Path
from typing import Protocol
from uuid import UUID

from papertrail.domain import (
    InputLineage,
    Job,
    JobCreateResult,
    JobFailure,
    JobSubmission,
    ProcessingResult,
)


class JobRepository(Protocol):
    async def create_or_reuse(
        self,
        submission: JobSubmission,
        idempotency_key: str,
    ) -> JobCreateResult: ...

    async def get(self, job_id: UUID) -> Job | None: ...

    async def claim(self, job_id: UUID) -> Job | None: ...

    async def reschedule(self, job_id: UUID, failure: JobFailure) -> Job: ...

    async def succeed(self, job_id: UUID, result: ProcessingResult) -> Job: ...

    async def fail(self, job_id: UUID, failure: JobFailure) -> Job: ...


class WorkQueue(Protocol):
    async def enqueue(self, job_id: UUID) -> None: ...

    async def dequeue(self) -> UUID: ...

    def depth(self) -> int: ...


class DownloadedDocument(Protocol):
    path: Path
    lineage: InputLineage


class DocumentFetcher(Protocol):
    async def fetch(self, document_url: str) -> DownloadedDocument: ...


class DocumentProcessor(Protocol):
    async def process(
        self,
        job: Job,
        document: DownloadedDocument,
    ) -> ProcessingResult: ...
