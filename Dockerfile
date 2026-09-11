# Shared image for cs-api, cs-worker and cs-scheduler -- same deps, same venv. The three
# differ only in which module they run, so render.yaml sets that per-service via
# `startCommand` rather than this image declaring one CMD. (cs-web is Next.js and has its
# own Dockerfile in web/ -- a completely different runtime.)
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# dbmate isn't in Debian's apt repos -- same static-binary approach deploy/ec2/setup.sh
# uses on the EC2 box this is replacing.
RUN curl -fsSL -o /usr/local/bin/dbmate \
      https://github.com/amacneil/dbmate/releases/latest/download/dbmate-linux-amd64 \
    && chmod +x /usr/local/bin/dbmate

RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src ./src
COPY db ./db

ENV PATH="/app/.venv/bin:${PATH}"
