# Builder container (with UV as package manager)
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:525eb0972b68435ca35014cf975e368b1a6cdd48fb0b5836866518b89863ca4c AS builder

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

RUN rm -f /etc/apt/apt.conf.d/docker-clean \
  && echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache
RUN --mount=target=/var/lib/apt/lists,type=cache,id=apt-lists-${TARGETPLATFORM},sharing=locked \
    apt-get update -q \
    && apt-get install -y -q --no-install-recommends \
        build-essential

WORKDIR /app

COPY . .

RUN --mount=target=/root/.cache/uv,type=cache,id=uv-${TARGETPLATFORM},sharing=locked \
    uv sync --frozen --no-dev

# Output container (with only venv and app source)
FROM python:3.13-slim-bookworm@sha256:ae9f9ac89467077ed1efefb6d9042132d28134ba201b2820227d46c9effd3174

WORKDIR /app

ENV PATH=/app/.venv/bin:$PATH

COPY --from=builder --chown=app:app /app .

ARG VERSION
ENV VERSION=${VERSION}

CMD ["bash", "-c", "gunicorn app.main:api --bind 0.0.0.0:8080 --graceful-timeout 60 --proxy-protocol --timeout 60 --worker-class uvicorn.workers.UvicornWorker --workers 4"]
