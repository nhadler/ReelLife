#!/bin/sh
# Installs or upgrades ReelLife for systemd (deploy/README.md).
# Run as root from the checkout at /opt/reellife:
#   sudo sh deploy/systemd/install.sh
# It doesn't start anything; fill in /etc/reellife first.
set -eu

APP=/opt/reellife
CONF=/etc/reellife
cd "$APP"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/" >&2
  exit 1
fi

# Python and the virtualenv live under /opt/reellife: the services run
# with ProtectHome=yes, so a Python under /root would be invisible.
umask 022
export UV_PYTHON_INSTALL_DIR="$APP/.python"
uv sync --frozen --no-dev --python 3.12
DJANGO_SETTINGS_MODULE=reellife.settings.build \
  "$APP/.venv/bin/reellife" collectstatic --noinput --clear >/dev/null

# Configuration: created once, never overwritten.
umask 077
mkdir -p "$CONF"
if [ ! -f "$CONF/secret_key" ]; then
  "$APP/.venv/bin/python" -c \
    'import secrets; print(secrets.token_urlsafe(50))' >"$CONF/secret_key"
fi
[ -f "$CONF/oidc_client_secret" ] || : >"$CONF/oidc_client_secret"
if [ ! -f "$CONF/reellife.env" ]; then
  grep -v -e '^REELLIFE_SECRET_KEY=' -e '^OIDC_CLIENT_SECRET=' \
    "$APP/deploy/.env.example" >"$CONF/reellife.env"
fi

umask 022
install -m 0644 deploy/systemd/reellife.service \
  deploy/systemd/reellife-snapshot.service \
  deploy/systemd/reellife-snapshot.timer /etc/systemd/system/
systemctl daemon-reload

cat <<MSG
Installed. Next:
  1. Edit $CONF/reellife.env and put the PocketID client secret in
     $CONF/oidc_client_secret.
  2. systemctl enable --now reellife.service reellife-snapshot.timer
MSG
