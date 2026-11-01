#!/bin/sh
# Entry point for the ReelLife image.
#   web        snapshot the database, migrate, then serve with gunicorn
#              (its ASGI worker: Django plus the MCP server at /mcp)
#   scheduler  run nightly maintenance until stopped
#   <other>    any reellife (manage.py) command, e.g. create_breakglass
set -eu

# Request bodies and uploads are spooled here (see the Dockerfile).
if [ -n "${TMPDIR:-}" ]; then
  mkdir -p "$TMPDIR"
  chmod 0700 "$TMPDIR"
fi

case "${1:-web}" in
  web)
    # Snapshot before migrating so an upgrade can be rolled back
    # (only when migrations are pending; kept apart from nightly ones).
    reellife snapshot --before-migrate
    reellife migrate --noinput
    reellife createcachetable
    exec gunicorn reellife.asgi:application \
      --worker-class asgi \
      --bind 0.0.0.0:8000 \
      --workers "${REELLIFE_WORKERS:-2}" \
      --worker-tmp-dir /dev/shm \
      --max-requests 1000 \
      --max-requests-jitter 100 \
      --access-logfile - \
      --error-logfile - \
      --forwarded-allow-ips "" \
      --no-control-socket
    ;;
  scheduler)
    exec reellife run_scheduler
    ;;
  *)
    exec reellife "$@"
    ;;
esac
