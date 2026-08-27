from datetime import UTC, datetime
from pathlib import Path

import pytest

from papertrail.analysis import AnalysisRun, DocumentAnalysis
from papertrail.artifacts import DownloadedDocument
from papertrail.domain import InputLineage, JobSubmission, ProcessingResult
from papertrail.pipeline import DocumentPipeline
from papertrail.repository import InMemoryJobRepository


@pytest.mark.asyncio
async def test_pipeline_records_lineage_and_analysis(tmp_path):
    source = tmp_path / "report.pdf"
    source.write_bytes(b"%PDF-demo")
    repository = InMemoryJobRepository()
    created = await repository.create_or_reuse(
        JobSubmission(document_url="https://example.org/report.pdf", operation="extract_markdown"),
        "pipeline-test",
    )

    class Fetcher:
        async def fetch(self, url):
            return DownloadedDocument(
                source,
                InputLineage(url, "input-hash", "application/pdf", source.stat().st_size),
            )

    class MinerU:
        async def process(self, job, document):
            return ProcessingResult("mineru", "3.4.5", {"task": "demo"}, {"mineru_result": {"md_content": "# Phishing\nUse multi-factor authentication."}})

    class Analyzer:
        async def analyze(self, markdown):
            analysis = DocumentAnalysis.model_validate({
                "topic": "security", "document_type": "fact_sheet", "language": "en",
                "summary": "The document gives phishing advice.", "keywords": ["phishing", "email", "security"],
                "actionability": "advisory", "entities": [], "commitments": [],
            })
            return AnalysisRun(analysis=analysis, route="review_queue", model="llama3.1:8b", prompt_version="test", duration_ms=12)

    result = await DocumentPipeline(repository, Fetcher(), MinerU(), Analyzer()).handle(created.job)
    stored = await repository.get(created.job.id)

    assert stored.input_lineage.sha256 == "input-hash"
    assert result.metadata["analysis"]["route"] == "review_queue"
    assert result.metadata["extraction"]["preview"].startswith("# Phishing")
    assert not source.exists()
