# LeMontage — slim image, built from source (no PyPI required).
# Build:  docker build -t lemontage .
# Run:    docker run --rm -v "$PWD":/work lemontage run pipeline.yaml
#
# Models (Whisper) and title fonts download on first run. Mount cache volumes to
# keep them between runs:
#   -v lemontage-cache:/root/.lemontage -v hf-cache:/root/.cache/huggingface
FROM python:3.12-slim

# fontconfig lets libass resolve fonts for titles/captions.
# FFmpeg itself is bundled via imageio-ffmpeg, so no system ffmpeg is needed.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fontconfig \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir ".[engine]"

# Pipelines resolve relative paths (input, ./output) against the mounted CWD.
WORKDIR /work
ENTRYPOINT ["lemontage"]
CMD ["--help"]
