# Deploying ReelLife

ReelLife supports **Docker Compose** and **Linux with systemd + uv**.
Both listen for HTTP on `127.0.0.1:8000` and require a TLS reverse proxy,
such as Tailscale Serve, Caddy or Traefik, for external access.

## Before either: PocketID

1. In PocketID, create an OIDC client for ReelLife:
   - type: confidential (it gets a client secret)
   - redirect URI: `https://<your host>/auth/oidc/callback/` (exactly
     `REELLIFE_BASE_URL` + `/auth/oidc/callback/`)
   - PKCE on
2. Create the groups `reellife-admins`, `reellife-editors` and (optional)
   `reellife-viewers`, and add people to them. Someone in none of them is
   refused at sign-in. Group names are configurable (`OIDC_GROUP_*`).
3. Note the issuer URL (e.g. `https://id.example.com`), client ID and
   secret.

ReelLife reads groups from the ID token or userinfo, whichever has them.
After the first real sign-in, check the role in the admin
(`/admin/accounts/user/`) to confirm PocketID sends the `groups` claim.

ReelLife re-checks each session with PocketID every 5 minutes and asks for
the `offline_access` scope, so it gets a refresh token to renew an expired
access token. If PocketID doesn't return one (check that a session
survives past the access token's lifetime, about an hour), sessions end
when the access token expires and people sign in again, usually without
typing anything.

## Configuration

Every setting is an environment variable; `deploy/.env.example` lists them
all with comments. Any variable can instead be read from a file by setting
`NAME_FILE=/path` (used for secrets below).

| Variable | Notes |
|---|---|
| `REELLIFE_SECRET_KEY` | 50+ random characters. |
| `REELLIFE_ALLOWED_HOSTS` | The host name(s) people use, comma-separated. |
| `REELLIFE_BASE_URL` | `https://...`; used for CSRF and the OIDC redirect. |
| `REELLIFE_TRUST_PROXY` | `true` when the proxy in front sets `X-Forwarded-Proto` and `X-Forwarded-For`. See "Proxies" below. |
| `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | From PocketID; the issuer must be `https://`. |
| `OIDC_GROUP_ADMIN`, `OIDC_GROUP_EDITOR`, `OIDC_GROUP_VIEWER` | Defaults `reellife-admins`, `reellife-editors`, `reellife-viewers`. |
| `OIDC_ACCOUNT_URL` | Optional: where the profile menu's Manage Account goes. Default: PocketID's account page, `OIDC_ISSUER` + `/settings/account`. |
| `REELLIFE_SOURCE_URL` | Optional: where Help's Source Code link goes. Default: this repository. If you run a modified copy for others, the AGPL asks you to point it at your source. |
| `REELLIFE_TIME_ZONE` | Where times are shown, as an IANA name such as `America/Los_Angeles`. Default `UTC`. Times are always stored in UTC, so changing it is safe. |
| `REELLIFE_BREAKGLASS_ENABLED` | Leave `false` (see "When PocketID is down"). |
| `REELLIFE_DATA_DIR` | Set for you: `/data` in Docker, `/var/lib/reellife` with systemd. |

`manage.py check --deploy` passes with no warnings on these settings, and
the app refuses to start if a required one is missing or `REELLIFE_BASE_URL`
or `OIDC_ISSUER` isn't https.

## Docker Compose

```sh
mkdir reellife && cd reellife
curl -fsSLO https://raw.githubusercontent.com/nhadler/reellife/main/deploy/docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/nhadler/reellife/main/deploy/.env.example -o .env
python3 -c 'import secrets; print(secrets.token_urlsafe(50))'  # REELLIFE_SECRET_KEY
$EDITOR .env
docker compose up -d
docker compose ps        # web should become "healthy"
```

The image is `ghcr.io/nhadler/reellife`, for `linux/amd64` and
`linux/arm64`, tagged by release (`1.0.0`, `1.0`, `1`) and `latest`. Each one
carries an SBOM and build provenance; check it came from this
repository's release workflow with
`gh attestation verify oci://ghcr.io/nhadler/reellife:1.0.0 --repo nhadler/reellife`.

To build from a checkout instead, add the build override:
`docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build`
(or `just up`).

- **Services:** `web` (gunicorn; snapshots the database, migrates, then
  serves) and `scheduler` (nightly snapshot and housekeeping at 03:00 UTC).
- **Data:** the `reellife-data` volume, mounted at `/data`: the SQLite
  database and `snapshots/`.
- **Hardening:** non-root UID 10001, read-only root filesystem with a
  `/tmp` tmpfs, all capabilities dropped, no-new-privileges, memory and PID
  limits, log rotation.
- **Uploads** are spooled to `/data/tmp` (`TMPDIR`), not the 64 MB `/tmp`
  tmpfs, which also counts against the memory limit; a backup being
  restored can be 200 MB. ReelLife refuses larger request bodies before
  reading them (12 MiB, or 201 MiB for an admin restoring a backup).
- **Options** in the same `.env` file: `REELLIFE_PORT` (host port, default
  8000), `REELLIFE_IMAGE` (the image, default
  `ghcr.io/nhadler/reellife:latest`; pin a release),
  `REELLIFE_WORKERS` (gunicorn workers, default 2).
- **Secrets as files:** instead of putting them in `.env`, mount Docker
  secrets and set `REELLIFE_SECRET_KEY_FILE` / `OIDC_CLIENT_SECRET_FILE`.
- **Logs:** `docker compose logs -f web` (JSON lines; audit events carry an
  `audit` object).
- **Commands:** `docker compose run --rm web <command>` runs any management
  command, e.g. `docker compose run --rm web create_breakglass`.

## Bare Linux with systemd + uv

Tested on Debian trixie with systemd 257; any recent systemd works.
Clone a release tag (below, `v1.0.0`) rather than `main`.

```sh
sudo git clone --branch v1.0.0 https://github.com/nhadler/reellife.git /opt/reellife
sudo sh /opt/reellife/deploy/systemd/install.sh
sudo $EDITOR /etc/reellife/reellife.env
echo '<pocketid client secret>' | sudo tee /etc/reellife/oidc_client_secret
sudo systemctl enable --now reellife.service reellife-snapshot.timer
curl -H 'Host: <your host>' http://127.0.0.1:8000/healthz   # ok
```

`install.sh` needs [uv](https://docs.astral.sh/uv/), and the PDF exports
need Pango and HarfBuzz:

```sh
sudo apt-get install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0
```

`install.sh` installs Python 3.12 and the locked dependencies under
`/opt/reellife` (the services can't see `/root`), collects static files,
creates `/etc/reellife` (with a generated `secret_key`) without
overwriting an existing one, and installs:

- `reellife.service`: gunicorn on `127.0.0.1:8000`. Before starting it
  snapshots the database, migrates and creates the cache table. It runs as
  a dynamic user with state in `/var/lib/reellife`, uploads spooled to
  `/var/cache/reellife` (`TMPDIR`), secrets passed with `LoadCredential=`,
  and a strict sandbox (`systemd-analyze security` rates it 1.5, "OK").
- `reellife-snapshot.service` + `.timer`: `reellife nightly` at 03:00
  local time, catching up after downtime.

Run management commands with the wrapper, which runs them as the service
user with its settings and secrets:

```sh
sudo /opt/reellife/deploy/systemd/reellife-manage create_breakglass
```

Logs: `journalctl -u reellife -f`.

## Proxies and TLS

ReelLife redirects plain HTTP to HTTPS (except `/healthz` and `/readyz`)
and sets HSTS, so it must sit behind TLS.

- **Caddy / Traefik:** set `REELLIFE_TRUST_PROXY=true`; both send
  `X-Forwarded-Proto` and `X-Forwarded-For`.
- **Tailscale Serve:** `tailscale serve --bg 8000`, and set
  `REELLIFE_ALLOWED_HOSTS` / `REELLIFE_BASE_URL` to the machine's
  `*.ts.net` name. **Check** that your Tailscale version sends
  `X-Forwarded-Proto`: open `https://<host>/auth/login/`. If the browser
  reports too many redirects, it doesn't; put Caddy between Tailscale and
  ReelLife (Caddy can use `tailscale cert` certificates) rather than
  turning off the HTTPS redirect.
- Only set `REELLIFE_TRUST_PROXY=true` when a proxy really sits in front:
  otherwise clients could claim to be on HTTPS or forge their IP in the
  audit log and rate limits.

## Backups

The host's backups should include the data directory, in particular
`snapshots/`. A live SQLite database can be caught mid-write by a file
copy; the snapshots can't. Each night ReelLife writes
`snapshots/reellife-YYYYMMDD.sqlite3` with SQLite's online backup API and
keeps the newest 14. A start with migrations to apply (an upgrade) first
writes `snapshots/before-migrate-YYYYMMDD-HHMMSS.sqlite3`, kept apart
from the nightly ones (the newest 5); restarts that change nothing don't
add more. They contain everything, including the audit log, so they're
readable only by the service user.

- Docker: the `reellife-data` volume (`docker volume inspect
  reellife_reellife-data` shows its path).
- systemd: `/var/lib/private/reellife` (`/var/lib/reellife` is a symlink).

The same nightly run also writes `backups/reellife-YYYYMMDD.zip` (keeping
14): a portable backup of every project, cue, version, setting, the change
history and the project thumbnails, the same file an admin gets from
**Backup** in the top bar. It leaves out the break-glass password and TOTP
by design.

**Restoring a backup ZIP** (e.g. moving to a new server): install the
same ReelLife version the backup came from, sign in as an admin, open
**Backup**, choose the file and type RESTORE. It only restores into an
install with no projects yet, snapshots the database first
(`snapshots/before-restore-….sqlite3`), and signs everyone out. Then set
up break-glass again with `create_breakglass` if you use it. To roll back
a working install instead, use a snapshot (below).

## Upgrades

Docker:

Read the release notes, set `REELLIFE_IMAGE` in `.env` to the new
release, then:

```sh
docker compose pull && docker compose up -d
```

From a checkout: `git pull`, then `just up`.

systemd:

```sh
cd /opt/reellife && sudo git fetch --tags && sudo git checkout v1.0.1   # the new release
sudo sh deploy/systemd/install.sh
sudo systemctl restart reellife.service
```

Either way the service snapshots the database (when there is anything
to migrate), then migrates. Read the CHANGELOG for manual steps first.

## Rollback

Migrations aren't always reversible, so roll back by restoring the
`before-migrate-….sqlite3` snapshot the upgrade took (the first one from
that time: a migration that failed partway and was retried may have
added another), then running the old version.

Docker (pinned releases make this easy: the previous image is the
previous tag):

```sh
docker compose down
docker compose run --rm --no-deps --entrypoint sh web -c \
  'ls /data/snapshots && cp /data/snapshots/before-migrate-YYYYMMDD-HHMMSS.sqlite3 /data/reellife.sqlite3 && rm -f /data/reellife.sqlite3-wal /data/reellife.sqlite3-shm'
# set REELLIFE_IMAGE in .env to the previous image, then
docker compose up -d
```

systemd:

```sh
sudo systemctl stop reellife.service
cd /opt/reellife && sudo git checkout <previous tag> && sudo sh deploy/systemd/install.sh
sudo cp /var/lib/private/reellife/snapshots/before-migrate-YYYYMMDD-HHMMSS.sqlite3 /var/lib/private/reellife/reellife.sqlite3
sudo rm -f /var/lib/private/reellife/reellife.sqlite3-wal /var/lib/private/reellife/reellife.sqlite3-shm
sudo systemctl start reellife.service
```

(The service's dynamic user owns the directory; the copied file keeps
the old file's owner because `cp` overwrites it in place.)

## When PocketID is down

1. Once, ahead of time: create the local admin and store its password and
   authenticator setup somewhere safe:
   `create_breakglass` (Docker: `docker compose run --rm web
   create_breakglass`; systemd: `reellife-manage create_breakglass`).
2. During the outage: set `REELLIFE_BREAKGLASS_ENABLED=true`, restart, and
   sign in at `/auth/local/` with the password and a current code. Every
   attempt is audit-logged and limited to 5 an hour.
3. Afterwards: set it back to `false` and restart. Existing break-glass
   sessions end immediately.

## Checks

- `just test-container` builds the image and tests it and the Compose
  file (non-root, read-only, healthy, serves sign-in, snapshots before
  migrating on start).
- `tests/large/test_systemd.py` (run by the same command) installs with
  `install.sh` on Debian with systemd as PID 1 and checks the units. systemd
  can't mount credential directories inside a container, so that test
  swaps `LoadCredential=` for readable test files; everything else runs as
  shipped.

## MCP server (AI assistants)

ReelLife serves an MCP server at `https://<your host>/mcp`. Claude
(desktop, Claude Code or claude.ai) and other MCP clients connect there
and sign in through PocketID; each client acts as the person who signed
in, with their ReelLife role.

In PocketID (v2.13 or later, for Client ID Metadata Documents):

1. Under **APIs**, add ReelLife with the resource URI
   `https://<your host>/mcp` exactly. Tokens for MCP clients are issued
   for it, and ReelLife refuses any token that wasn't.
2. Let the client register:
   - **Clients that publish a Client ID Metadata Document:** add the
     client's metadata URL pattern to PocketID's CIMD allowlist.
   - **Others:** create an OIDC client for it (public, PKCE) and give
     the client its ID.
   Either way, allow the client to request the ReelLife API.
3. People need one of the ReelLife groups, as for the web app.

A role change or removal in PocketID reaches MCP within five minutes.
