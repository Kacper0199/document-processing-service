import asyncio
from urllib.parse import urljoin

import httpx

from papertrail.domain import JobFailure, ProcessingResult
from papertrail.worker import ProcessingError


class MinerUProcessor:
    def __init__(self, base_url, *, poll_interval_seconds=1, timeout_seconds=30):
        self._base_url = base_url.rstrip("/") + "/"
        self._poll_interval_seconds = poll_interval_seconds
        self._timeout = httpx.Timeout(timeout_seconds)

    async def process(self, job, document):
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            task_id = await self._submit(client, document)
            result = await self._wait_for_result(client, task_id)
        return ProcessingResult(
            processor="mineru", processor_version="3.4.5",
            artifacts={"mineru_task": task_id, "mineru_result": urljoin(self._base_url, f"tasks/{task_id}/result")},
            metadata={"mineru_result": result, "source_sha256": document.lineage.sha256},
        )

    async def _submit(self, client, document):
        with document.path.open("rb") as source:
            response = await client.post(
                urljoin(self._base_url, "tasks"), data={"return_md": "true"},
                files={"files": (document.path.name, source, document.lineage.content_type)},
            )
        if response.status_code >= 500:
            raise self._failure("mineru_unavailable", "The document processor is unavailable.", True)
        if response.status_code >= 400:
            raise self._failure("mineru_rejected_document", "The document processor rejected the document.", False)
        task_id = response.json().get("task_id") or response.json().get("id")
        if not isinstance(task_id, str):
            raise self._failure("mineru_protocol_error", "The document processor returned an invalid response.", True)
        return task_id

    async def _wait_for_result(self, client, task_id):
        while True:
            response = await client.get(urljoin(self._base_url, f"tasks/{task_id}"))
            if response.status_code >= 500:
                raise self._failure("mineru_unavailable", "The document processor is unavailable.", True)
            if response.status_code >= 400:
                raise self._failure("mineru_task_missing", "The document processor lost the task.", True)
            state = str(response.json().get("status") or response.json().get("state") or "").lower()
            if state in {"completed", "succeeded", "success", "done"}:
                result = await client.get(urljoin(self._base_url, f"tasks/{task_id}/result"))
                if result.status_code >= 500:
                    raise self._failure("mineru_unavailable", "The document processor is unavailable.", True)
                if result.status_code >= 400:
                    raise self._failure("mineru_result_missing", "The document processor did not return a result.", True)
                return result.json()
            if state in {"failed", "error", "cancelled"}:
                raise self._failure("mineru_processing_failed", "The document processor could not process the document.", False)
            await asyncio.sleep(self._poll_interval_seconds)

    @staticmethod
    def _failure(code, message, retryable):
        return ProcessingError(JobFailure(code, message, retryable))
