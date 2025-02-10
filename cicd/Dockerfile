# Builder container (with UV as package manager)
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:525eb0972b68435ca35014cf975e368b1a6cdd48fb0b5836866518b89863ca4c AS build

# UV build dependencies
RUN --mount=target=/var/lib/apt/lists,type=cache,id=apt-lists-${TARGETPLATFORM},sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential

# Copy project definitions
WORKDIR /app
COPY pyproject.toml uv.lock ./

# Install Python dependencies
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
RUN --mount=target=/root/.cache/uv,type=cache,id=uv-${TARGETPLATFORM},sharing=locked \
    uv sync --frozen --no-dev

# Output container (with only venv and app source)
FROM python:3.13-slim-bookworm@sha256:ae9f9ac89467077ed1efefb6d9042132d28134ba201b2820227d46c9effd3174

# Set default dir
WORKDIR /app

# Copy sources and venv
COPY . .
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

# Allow app to know its version
ARG VERSION
ENV VERSION=${VERSION}

# Starting the backend
CMD gunicorn app.main:api --bind 0.0.0.0:8080 --graceful-timeout 60 --proxy-protocol --timeout 60 --worker-class uvicorn.workers.UvicornWorker --workers 4
