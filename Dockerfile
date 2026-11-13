# syntax=docker/dockerfile:1
# ReelTime image. Published for amd64 and arm64 at
# ghcr.io/nhadler/reeltime by .github/workflows/release.yml. Build locally
# from the repository root:
#   docker build -t reeltime:local .

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS build
# uv from PyPI, pinned to the version that wrote uv.lock. It stays in this
# stage; the runtime image gets only the built virtualenv.
RUN pip install --no-cache-dir uv==0.12.17
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY manage.py ./
COPY accounts ./accounts
COPY cues ./cues
COPY reeltime ./reeltime
COPY mcp_server ./mcp_server
COPY templates ./templates
COPY deploy/docker-entrypoint.sh ./deploy/docker-entrypoint.sh
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev \
 && DJANGO_SETTINGS_MODULE=reeltime.settings.build \
    /app/.venv/bin/reeltime collectstatic --noinput


FROM ${PYTHON_IMAGE} AS runtime
# The release workflow adds the version, revision and creation time.
LABEL org.opencontainers.image.title="ReelTime" \
      org.opencontainers.image.description="Self-hosted music cue tracking for film and TV composers." \
      org.opencontainers.image.source="https://github.com/nhadler/reeltime" \
      org.opencontainers.image.documentation="https://github.com/nhadler/reeltime/blob/main/deploy/README.md" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"
# Pango and HarfBuzz for the PDF exports (WeasyPrint). PDFs use the app's
# own fonts, so no font packages.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
 && rm -rf /var/lib/apt/lists/*
# A fixed, unprivileged user; the app files stay owned by root, so the
# process can't modify its own code.
RUN groupadd --system --gid 10001 reeltime \
 && useradd --system --uid 10001 --gid reeltime --no-create-home \
      --home-dir /nonexistent --shell /usr/sbin/nologin reeltime \
 && mkdir /data \
 && chown reeltime:reeltime /data \
 && chmod 0700 /data
COPY --from=build /app /app

# Uploads (up to 200 MB for a backup being restored) are spooled to
# TMPDIR. It is on the data volume because /tmp is a small tmpfs, and a
# tmpfs counts against the container's memory limit; the entry point
# creates it. Fontconfig (PDF exports) keeps its small cache under
# XDG_CACHE_HOME: on the tmpfs, since the rest of the image is read-only.
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=reeltime.settings.prod \
    REELTIME_DATA_DIR=/data \
    TMPDIR=/data/tmp \
    XDG_CACHE_HOME=/tmp/cache
USER 10001:10001
WORKDIR /app
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-m", "reeltime.healthcheck"]
ENTRYPOINT ["/app/deploy/docker-entrypoint.sh"]
CMD ["web"]
