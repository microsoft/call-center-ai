# Base container
FROM ghcr.io/astral-sh/uv:python3.12-bookworm AS base

ARG VERSION
ENV VERSION=${VERSION}

RUN useradd -m appuser

COPY --chown=appuser:appuser . /app

WORKDIR /app

RUN --mount=target=/root/.cache/uv,type=cache,id=uv-${TARGETPLATFORM},sharing=locked \
    uv sync --frozen

USER appuser

ENV PATH=/app/.venv/bin:$PATH

CMD ["bash", "-c", "gunicorn app.main:api --bind 0.0.0.0:8080 --proxy-protocol --workers 4 --worker-class uvicorn.workers.UvicornWorker"]
