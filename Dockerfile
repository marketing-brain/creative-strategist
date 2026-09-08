FROM python:3.12-slim

# System deps beyond what uv/pip installs:
#   ffmpeg  - video keyframe/audio extraction (tagging/video.py)
#   nodejs  - runs tools/video-gen/index.mjs, which calls the AI SDK's
#             generateVideo() (generation/video.py shells out to it)
#   dbmate  - runs schema migrations as this image's preDeployCommand
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && curl -fsSL -o /usr/local/bin/dbmate \
      https://github.com/amacneil/dbmate/releases/latest/download/dbmate-linux-amd64 \
    && chmod +x /usr/local/bin/dbmate \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY tools/video-gen/package.json tools/video-gen/package-lock.json tools/video-gen/
RUN cd tools/video-gen && npm ci --omit=dev

COPY src ./src
COPY db ./db

ENV PATH="/app/.venv/bin:${PATH}"

# Overridden per-service by render.yaml's `dockerCommand` for the worker and
# scheduler services -- this default runs the API.
CMD ["python", "-m", "cs.api"]
