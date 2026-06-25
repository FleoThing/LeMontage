# LeMontage - multi-stage image with a cacheable build layer and a minimal runtime.
# Build:  docker build -t lemontage .
# Run:    docker run --rm -v "$PWD":/work lemontage run pipeline.yaml
#
# Models (Whisper) and title fonts download on first run. Mount cache volumes to
# keep them between runs:
#   -v lemontage-cache:/root/.lemontage -v hf-cache:/root/.cache/huggingface

# Builder stage: install dependencies into an isolated virtualenv so the runtime
# only has to copy one directory.
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends fontconfig \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /src

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
 && pip install ".[engine]"

# Runtime stage: distroless keeps the final image small and reduces the attack
# surface. The installed virtualenv already contains the entrypoint and deps.
FROM gcr.io/distroless/python3-debian13:nonroot AS runtime

ARG BUILD_DATE=unknown
ARG VCS_REF=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"

LABEL org.opencontainers.image.title="LeMontage" \
      org.opencontainers.image.description="Pipeline-first framework for content creators" \
      org.opencontainers.image.source="https://github.com/ffillouxdev/LeMontage" \
      org.opencontainers.image.url="https://github.com/ffillouxdev/LeMontage" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# Pipelines resolve relative paths (input, ./output) against the mounted CWD.
WORKDIR /work
ENTRYPOINT ["/opt/venv/bin/lemontage"]
CMD ["--help"]
