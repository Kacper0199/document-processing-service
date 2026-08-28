from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from papertrail.app import create_app
from papertrail.domain import JobSubmission, Operation
from papertrail.jobs import JobService
from papertrail.repository import InMemoryJobRepository


def test_reuses_job_for_same_idempotency_key():
    client = TestClient(create_app(JobService(InMemoryJobRepository())))
    body = {"document_url": "https://example.org/report.pdf", "operation": "extract_markdown"}
    headers = {"Idempotency-Key": "report-2026"}

    first = client.post("/jobs", json=body, headers=headers)
    second = client.post("/jobs", json=body, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert second.json()["reused"] is True


def test_rejects_changed_request_for_same_key():
    client = TestClient(create_app(JobService(InMemoryJobRepository())))
    headers = {"Idempotency-Key": "report-2026"}

    client.post("/jobs", json={"document_url": "https://example.org/one.pdf", "operation": "extract_markdown"}, headers=headers)
    response = client.post("/jobs", json={"document_url": "https://example.org/two.pdf", "operation": "extract_markdown"}, headers=headers)

    assert response.status_code == 409


def test_lists_job_status_resources_newest_first():
    client = TestClient(create_app(JobService(InMemoryJobRepository())))
    first = client.post(
        "/jobs",
        json={"document_url": "https://example.org/first.pdf", "operation": "extract_markdown"},
        headers={"Idempotency-Key": "first"},
    ).json()
    second = client.post(
        "/jobs",
        json={"document_url": "https://example.org/second.pdf", "operation": "extract_markdown"},
        headers={"Idempotency-Key": "second"},
    ).json()

    response = client.get("/jobs")

    assert response.status_code == 200
    assert [job["id"] for job in response.json()] == [second["job_id"], first["job_id"]]
    assert response.json()[0]["state"] == "queued"


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:5173"])
def test_allows_dashboard_origins_for_job_requests(origin):
    client = TestClient(create_app(JobService(InMemoryJobRepository())))

    response = client.options(
        "/jobs",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,idempotency-key",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "POST" in response.headers["access-control-allow-methods"]


def test_rejects_foreign_origin_for_job_requests():
    client = TestClient(create_app(JobService(InMemoryJobRepository())))

    response = client.options(
        "/jobs",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.asyncio
async def test_repository_lists_newest_jobs_with_a_bound():
    repository = InMemoryJobRepository()
    submission = JobSubmission(
        document_url="https://example.org/report.pdf", operation=Operation.EXTRACT_MARKDOWN
    )
    first = await repository.create_or_reuse(submission, "first")
    second = await repository.create_or_reuse(submission, "second")

    jobs = await repository.list(limit=1)

    assert jobs == [second.job]
    assert first.job.created_at <= second.job.created_at
