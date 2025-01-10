# Builder container (with UV as package manager)
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:aacf61c53ed988e4a32b8b4da19043fdce7a8efcc67fb21ebc4cc0ba85f335b3 AS builder

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
FROM python:3.12-slim-bookworm@sha256:10f3aaab98db50cba827d3b33a91f39dc9ec2d02ca9b85cbc5008220d07b17f3

WORKDIR /app

ENV PATH=/app/.venv/bin:$PATH

COPY --from=builder --chown=app:app /app .

ARG VERSION
ENV VERSION=${VERSION}

CMD ["bash", "-c", "gunicorn app.main:api --bind 0.0.0.0:8080 --graceful-timeout 60 --proxy-protocol --timeout 60 --worker-class uvicorn.workers.UvicornWorker --workers 4"]
