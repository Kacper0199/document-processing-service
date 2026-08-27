import hashlib

from papertrail.domain import JobFailure
from papertrail.worker import ProcessingError


class DocumentPipeline:
    def __init__(self, repository, fetcher, processor, analyzer):
        self._repository = repository
        self._fetcher = fetcher
        self._processor = processor
        self._analyzer = analyzer

    async def handle(self, job):
        document = await self._fetcher.fetch(job.document_url)
        await self._repository.record_input(job.id, document.lineage)
        try:
            extraction = await self._processor.process(job, document)
            markdown = self._markdown_from(extraction.metadata.get("mineru_result"))
            if not markdown:
                raise ProcessingError(
                    JobFailure("mineru_missing_markdown", "MinerU did not return Markdown output.", True)
                )
            analysis = await self._analyzer.analyze(markdown)
            metadata = {
                "extraction": {
                    "sha256": hashlib.sha256(markdown.encode()).hexdigest(),
                    "characters": len(markdown),
                    "preview": markdown[:1000],
                },
                "analysis": analysis.model_dump(mode="json"),
            }
            return extraction.__class__(
                extraction.processor,
                extraction.processor_version,
                extraction.artifacts,
                metadata,
            )
        finally:
            document.path.unlink(missing_ok=True)

    @staticmethod
    def _markdown_from(value):
        if isinstance(value, dict):
            for key in ("markdown", "md_content", "md", "content"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate
            for nested in value.values():
                markdown = DocumentPipeline._markdown_from(nested)
                if markdown:
                    return markdown
        if isinstance(value, list):
            for nested in value:
                markdown = DocumentPipeline._markdown_from(nested)
                if markdown:
                    return markdown
        return None
