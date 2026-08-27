from pathlib import Path

import pytest

from papertrail.fetcher import SafeDocumentFetcher
from papertrail.worker import ProcessingError


@pytest.mark.asyncio
async def test_blocks_loopback_url(tmp_path):
    fetcher = SafeDocumentFetcher(
        Path(tmp_path), connect_timeout_seconds=1, read_timeout_seconds=1, max_bytes=1024
    )

    with pytest.raises(ProcessingError) as error:
        await fetcher.fetch("http://127.0.0.1/report.pdf")

    assert error.value.failure.code == "unsafe_document_url"
