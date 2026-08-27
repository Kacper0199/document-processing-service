# Document Processing Service

This project is an asynchronous service for processing documents from a URL. It accepts a request, creates a job, and processes the document outside the HTTP request. The client can check the job status while the work is running.

The document processor is MinerU. MinerU is an open-source tool that extracts structured content from documents such as PDFs, images, DOCX, PPTX, and XLSX files. In this project, the service downloads a document first and then sends the local file to the MinerU API. The service does not pass a user-provided URL directly to MinerU.

The project uses FastAPI, an in-memory queue and repository, a MinerU adapter, and a small React dashboard. It is a local demonstration service, not a complete production platform.

## Features

- `POST /jobs` accepts a document URL and returns `202 Accepted` with a job ID.
- `GET /jobs/{id}` returns the current job state.
- `Idempotency-Key` prevents duplicate jobs for the same request.
- Retryable errors are retried up to three times.
- The service provides liveness, readiness, Prometheus metrics, and JSON request logs.
- The document fetcher checks the URL, redirects, content type, and response size before processing.
- The React dashboard can submit a job and display its status.

## Run locally

The API uses Python 3.13 and uv.

```bash
uv sync
uv run uvicorn papertrail.app:create_app --factory --host 127.0.0.1 --port 8080
```

Start the dashboard in another terminal:

```bash
cd dashboard
npm install
npm run dev
```

Vite prints the dashboard URL. The dashboard uses `http://localhost:8080` by default. Set `VITE_API_URL` when the API runs at a different address.

## Submit a job

```bash
curl -i http://127.0.0.1:8080/jobs \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: report-001' \
  -d '{"document_url":"https://example.org/report.pdf","operation":"extract_markdown"}'
```

The first request returns `202 Accepted`. Sending the same request with the same idempotency key returns the existing job and sets `reused` to `true`.

```bash
curl http://127.0.0.1:8080/jobs/JOB_ID
curl http://127.0.0.1:8080/health/live
curl http://127.0.0.1:8080/health/ready
curl http://127.0.0.1:8080/metrics
```

## MinerU on macOS

MinerU runs separately from the application container. Its Docker setup is intended for Linux or WSL2 GPU environments. Docker Desktop on macOS cannot provide MPS or MLX acceleration to a MinerU container.

For this workspace, MinerU is installed locally under `.runtime/`. The model files and MinerU configuration remain in this directory and are not committed to the repository.

```bash
HOME="$PWD/.runtime/home" HF_HOME="$PWD/.runtime/huggingface" \
  .runtime/mineru-venv/bin/mineru-api --host 127.0.0.1 --port 8000
```

The service uses `http://127.0.0.1:8000` as the default MinerU address. Set `PAPERTRAIL_MINERU_BASE_URL` when MinerU runs elsewhere.

## Docker

```bash
docker compose up --build
```

The API is available on port 8080. On macOS, Compose uses `host.docker.internal:8000` to reach the native MinerU service. Start MinerU first. On Linux, set `PAPERTRAIL_MINERU_BASE_URL` to the address of a separate MinerU service.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `PAPERTRAIL_MINERU_BASE_URL` | `http://127.0.0.1:8000` | MinerU API address |
| `PAPERTRAIL_WORK_DIR` | `.papertrail-data` | Temporary downloaded files |
| `PAPERTRAIL_FETCH_MAX_BYTES` | `10000000` | Maximum download size in bytes |
| `PAPERTRAIL_RETRY_LIMIT` | `3` | Maximum number of attempts |

## Tests

```bash
uv run pytest -q
uv run coverage run -m pytest -q
uv run coverage report
```

The tests cover API idempotency, worker retry behavior, and loopback URL blocking. They do not run live MinerU inference.

## Design choices and limits

The repository and queue are in memory to keep the local setup simple. Both are behind interfaces, so they can later be replaced with a database, Redis, or a separate task queue without changing the API contract.

The `stable` and `candidate` processor configuration shows a simple promotion and rollback pattern. It does not replace a full model-serving system.

The current version has a few known limits. State is lost after restart, one worker processes the queue, and MinerU stores its own result artifacts. The URL checks reduce SSRF risk. A production deployment should also use network-level egress controls.
