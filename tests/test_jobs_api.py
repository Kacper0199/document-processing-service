from fastapi.testclient import TestClient

from papertrail.app import create_app
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
