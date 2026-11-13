#!/bin/sh
# Installs or upgrades ReelTime for systemd (deploy/README.md).
# Run as root from the checkout at /opt/reeltime:
#   sudo sh deploy/systemd/install.sh
# It doesn't start anything; fill in /etc/reeltime first.
set -eu

APP=/opt/reeltime
CONF=/etc/reeltime
cd "$APP"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/" >&2
  exit 1
fi

# Python and the virtualenv live under /opt/reeltime: the services run
# with ProtectHome=yes, so a Python under /root would be invisible.
umask 022
export UV_PYTHON_INSTALL_DIR="$APP/.python"
uv sync --frozen --no-dev --python 3.12
DJANGO_SETTINGS_MODULE=reeltime.settings.build \
  "$APP/.venv/bin/reeltime" collectstatic --noinput --clear >/dev/null

# Configuration: created once, never overwritten.
umask 077
mkdir -p "$CONF"
if [ ! -f "$CONF/secret_key" ]; then
  "$APP/.venv/bin/python" -c \
    'import secrets; print(secrets.token_urlsafe(50))' >"$CONF/secret_key"
fi
[ -f "$CONF/oidc_client_secret" ] || : >"$CONF/oidc_client_secret"
if [ ! -f "$CONF/reeltime.env" ]; then
  grep -v -e '^REELTIME_SECRET_KEY=' -e '^OIDC_CLIENT_SECRET=' \
    "$APP/deploy/.env.example" >"$CONF/reeltime.env"
fi

umask 022
install -m 0644 deploy/systemd/reeltime.service \
  deploy/systemd/reeltime-snapshot.service \
  deploy/systemd/reeltime-snapshot.timer /etc/systemd/system/
systemctl daemon-reload

cat <<MSG
Installed. Next:
  1. Edit $CONF/reeltime.env and put the PocketID client secret in
     $CONF/oidc_client_secret.
  2. systemctl enable --now reeltime.service reeltime-snapshot.timer
MSG
