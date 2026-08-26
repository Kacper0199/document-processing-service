class DocumentPipeline:
    def __init__(self, fetcher, processor):
        self._fetcher = fetcher
        self._processor = processor

    async def handle(self, job):
        document = await self._fetcher.fetch(job.document_url)
        try:
            return await self._processor.process(job, document)
        finally:
            document.path.unlink(missing_ok=True)
