import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CollectorRegistry, Gauge, make_asgi_app

from papertrail.config import Settings
from papertrail.domain import JobAccepted, JobStatus, JobSubmission
from papertrail.fetcher import SafeDocumentFetcher
from papertrail.jobs import JobService
from papertrail.mineru import MinerUProcessor
from papertrail.ollama import OllamaAnalyzer
from papertrail.pipeline import DocumentPipeline
from papertrail.repository import IdempotencyConflictError, InMemoryJobRepository
from papertrail.worker import InMemoryWorkQueue, WorkerSupervisor

logger = logging.getLogger("papertrail")
JOB_LIST_LIMIT = 100


def create_app(service=None, worker=None) -> FastAPI:
    if service is None:
        settings = Settings()
        queue = InMemoryWorkQueue()
        repository = InMemoryJobRepository()
        fetcher = SafeDocumentFetcher(
            settings.work_dir,
            connect_timeout_seconds=settings.fetch_connect_timeout_seconds,
            read_timeout_seconds=settings.fetch_read_timeout_seconds,
            max_bytes=settings.fetch_max_bytes,
        )
        pipeline = DocumentPipeline(
            repository,
            fetcher,
            MinerUProcessor(str(settings.mineru_base_url)),
            OllamaAnalyzer(str(settings.ollama_base_url), settings.ollama_model),
        )
        worker = WorkerSupervisor(repository, queue, pipeline.handle, retry_limit=settings.retry_limit)
        service = JobService(repository, queue)

    @asynccontextmanager
    async def lifespan(app):
        if worker:
            await worker.start()
        yield
        if worker:
            await worker.stop()

    app = FastAPI(title="Papertrail", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Idempotency-Key"],
    )
    registry = CollectorRegistry()
    queue_depth = Gauge("papertrail_queue_depth", "Jobs waiting in the in-memory queue", registry=registry)
    if worker and hasattr(service, "_queue") and service._queue:
        queue_depth.set_function(service._queue.depth)
    app.mount("/metrics", make_asgi_app(registry=registry))

    @app.middleware("http")
    async def log_request(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        logger.info(json.dumps({"event": "request_completed", "method": request.method, "path": request.url.path, "status": response.status_code, "duration_ms": round((time.perf_counter() - started) * 1000, 2)}))
        return response

    @app.get("/health/live")
    async def live():
        return {"status": "live"}

    @app.get("/health/ready")
    async def ready():
        if worker and worker.is_ready:
            return {"status": "ready"}
        raise HTTPException(status_code=503, detail="worker_not_ready")

    @app.post("/jobs", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
    async def create_job(submission: JobSubmission, idempotency_key: Annotated[str, Header(alias="Idempotency-Key")]):
        _validate_idempotency_key(idempotency_key)
        try:
            result = await service.submit(submission, idempotency_key)
        except IdempotencyConflictError as error:
            raise HTTPException(status_code=409, detail="idempotency_key_conflict") from error
        return JobAccepted(job_id=result.job.id, state=result.job.state, reused=result.reused)

    @app.get("/jobs", response_model=list[JobStatus])
    async def list_jobs():
        return [_to_status(job) for job in await service.list(JOB_LIST_LIMIT)]

    @app.get("/jobs/{job_id}", response_model=JobStatus)
    async def get_job(job_id: UUID):
        job = await service.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job_not_found")
        return _to_status(job)

    return app


def _validate_idempotency_key(value):
    if not 1 <= len(value) <= 128 or any(ord(character) < 33 or ord(character) > 126 for character in value):
        raise HTTPException(status_code=400, detail="invalid_idempotency_key")


def _to_status(job):
    return JobStatus(id=job.id, state=job.state, operation=job.operation, attempt_count=job.attempt_count, created_at=job.created_at, updated_at=job.updated_at, started_at=job.started_at, finished_at=job.finished_at, input_lineage=job.input_lineage, result=job.result, failure=job.failure)
