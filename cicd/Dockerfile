# Base container
FROM ghcr.io/astral-sh/uv:python3.12-bookworm AS base

RUN rm -f /etc/apt/apt.conf.d/docker-clean \
  && echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache
RUN --mount=target=/var/lib/apt/lists,type=cache,id=apt-lists-${TARGETPLATFORM},sharing=locked --mount=target=/root/.cache/pip,type=cache,id=pip-${TARGETPLATFORM},sharing=locked \
    apt-get update -q \
    && apt-get install -y -q --no-install-recommends \
        ffmpeg

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
