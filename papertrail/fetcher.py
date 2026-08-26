import asyncio
import hashlib
import ipaddress
import socket
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from papertrail.artifacts import DownloadedDocument
from papertrail.domain import InputLineage, JobFailure
from papertrail.worker import ProcessingError

_ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "image/jpeg", "image/png", "image/tiff",
}


class SafeDocumentFetcher:
    def __init__(self, work_dir, *, connect_timeout_seconds, read_timeout_seconds, max_bytes, max_redirects=3):
        self._work_dir = work_dir
        self._timeout = httpx.Timeout(read_timeout_seconds, connect=connect_timeout_seconds)
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects

    async def fetch(self, document_url):
        current_url = document_url
        for redirect_count in range(self._max_redirects + 1):
            await self._validate_url(current_url)
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=False) as client:
                async with client.stream("GET", current_url, headers={"Accept": ", ".join(_ALLOWED_CONTENT_TYPES)}) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location or redirect_count == self._max_redirects:
                            raise self._failure("redirect_not_allowed", "The document redirect could not be followed.", False)
                        current_url = urljoin(current_url, location)
                        continue
                    if response.status_code >= 500:
                        raise self._failure("document_host_unavailable", "The document host is unavailable.", True)
                    if response.status_code != 200:
                        raise self._failure("document_unavailable", "The document could not be retrieved.", False)
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if content_type not in _ALLOWED_CONTENT_TYPES:
                        raise self._failure("unsupported_document_type", "The document type is not supported.", False)
                    return await self._write_document(response, current_url, content_type)
        raise self._failure("redirect_not_allowed", "The document redirect could not be followed.", False)

    async def _write_document(self, response, document_url, content_type):
        self._work_dir.mkdir(parents=True, exist_ok=True)
        digest, size_bytes = hashlib.sha256(), 0
        with tempfile.NamedTemporaryFile(dir=self._work_dir, delete=False) as output:
            path = Path(output.name)
            try:
                async for chunk in response.aiter_bytes():
                    size_bytes += len(chunk)
                    if size_bytes > self._max_bytes:
                        raise self._failure("document_too_large", "The document exceeds the size limit.", False)
                    digest.update(chunk)
                    output.write(chunk)
            except BaseException:
                path.unlink(missing_ok=True)
                raise
        return DownloadedDocument(path, InputLineage(document_url, digest.hexdigest(), content_type, size_bytes))

    async def _validate_url(self, value):
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise self._failure("invalid_document_url", "The document URL is not allowed.", False)
        addresses = await self._resolve_addresses(parsed.hostname)
        if not addresses or any(not address.is_global for address in addresses):
            raise self._failure("unsafe_document_url", "The document URL is not allowed.", False)

    @staticmethod
    async def _resolve_addresses(hostname):
        records = await asyncio.to_thread(socket.getaddrinfo, hostname, None, type=socket.SOCK_STREAM)
        return {ipaddress.ip_address(record[4][0]) for record in records}

    @staticmethod
    def _failure(code, message, retryable):
        return ProcessingError(JobFailure(code, message, retryable))
