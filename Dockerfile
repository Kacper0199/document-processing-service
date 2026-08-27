FROM ghcr.io/astral-sh/uv:0.10.4 AS uv
FROM python:3.13.1-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy
WORKDIR /app
COPY --from=uv /uv /uvx /bin/
RUN useradd --create-home --uid 10001 app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY papertrail ./papertrail
COPY config ./config
RUN uv sync --frozen --no-dev

USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8080/health/live')"
CMD [".venv/bin/uvicorn", "papertrail.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
