# Common tasks. Run `just --list` to see them.

# Run the app with the fake OIDC provider. Open http://127.0.0.1:8000.
dev:
    #!/usr/bin/env bash
    set -euo pipefail
    uv run python manage.py migrate
    uv run python manage.py createcachetable
    uv run python -m tests.fakes.oidc_provider &
    trap 'kill $!' EXIT
    uv run python manage.py runserver 127.0.0.1:8000

# Run the small and medium test suites.
test *args:
    uv run pytest -m "not large" -n auto {{args}}

# Run tests with coverage and enforce the floors. The 100%
# list is in scripts/coverage_floors.sh, which CI runs too.
cov:
    uv run pytest -m "not large" -n auto --cov --cov-report=term
    sh scripts/coverage_floors.sh

# Every lint and type gate CI runs.
lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run pylint accounts cues mcp_server reeltime tests manage.py
    uv run mypy
    uv run bandit -q -r accounts cues mcp_server reeltime -x tests
    uv run python manage.py makemigrations --check --dry-run
    just check-deploy
    npx --no-install eslint .
    npx --no-install prettier --check .

# `check --deploy` against production settings with placeholder values.
check-deploy:
    REELTIME_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))') \
    REELTIME_ALLOWED_HOSTS=reeltime.example.com \
    REELTIME_BASE_URL=https://reeltime.example.com \
    REELTIME_DATA_DIR=$(mktemp -d) \
    OIDC_ISSUER=https://id.example.com \
    OIDC_CLIENT_ID=reeltime \
    OIDC_CLIENT_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))') \
    DJANGO_SETTINGS_MODULE=reeltime.settings.prod \
    uv run python manage.py check --deploy --fail-level WARNING

# Build the image (Dockerfile) as reeltime:local.
build:
    docker build -t reeltime:local .

# Start the Compose deployment from this checkout (needs deploy/.env).
up:
    cd deploy && docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build

# Large tests: build the image and test it and the Compose file.
test-container:
    uv run pytest -m large tests/large -p no:xdist -v --ignore=tests/large/e2e

# End-to-end tests in a real browser (Playwright + axe-core).
# First time: `uv run playwright install chromium` and `npm ci`.
e2e *args:
    uv run pytest -m large tests/large/e2e -p no:xdist {{args}}

# Re-copy pinned front-end files (versions from package.json) into
# cues/static/cues/vendor and refresh VERSIONS.
vendor:
    npm ci
    uv run python scripts/vendor_assets.py

# Build the demo project (3 reels, 2 turnovers, 40 cues), replacing it.
seed:
    uv run python manage.py seed_demo --replace

# Format and auto-fix.
fmt:
    uv run ruff format .
    uv run ruff check --fix .
    npx --no-install prettier --write .
    npx --no-install eslint --fix .

# Install Python and JS tooling and the git hooks.
setup:
    uv sync
    npm ci
    uv run pre-commit install

# Audit locked dependencies for known vulnerabilities.
audit:
    uv export --frozen --no-hashes --no-emit-project --format requirements-txt | uv run pip-audit --strict -r /dev/stdin
