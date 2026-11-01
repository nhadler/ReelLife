# ReelLife

[![CI](https://github.com/nhadler/reellife/actions/workflows/ci.yml/badge.svg)](https://github.com/nhadler/reellife/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/nhadler/reellife?sort=semver)](https://github.com/nhadler/reellife/releases)
[![Image](https://img.shields.io/badge/image-ghcr.io%2Fnhadler%2Freellife-blue)](https://github.com/nhadler/reellife/pkgs/container/reellife)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue)](LICENSE)

**Self-hosted music cue tracking for film and TV composers**

![The cue grid: reels on a timeline, every cue with its status, timecodes and picture](docs/images/cue-grid.png)

## What it does

- **Cues in an editable grid.** Reels on a timeline, statuses in colour,
  timecodes at any frame rate (drop-frame included), views for spotting,
  conform, orchestration, recording, mixing and the cue sheet. Undo for
  every change.
- **Versions.** A cue is a moment in the picture; each version is one
  delivery, with its own notes, themes and the picture it was written to.
- **Imports** from Pro Tools (Session Info as Text, clips or markers),
  Cubase and Nuendo, MIDI markers, and Excel or CSV, with a preview of
  every new, changed and untouched cue.
- **Conform** to a new cut: see which cues are on older picture and shift
  them by reel.
- **Exports:** cue logs (Excel, CSV), PDF reports, and RapidCue cue
  sheets with writers, publishers and shares.
- **Schedule.** What to write each day across every project, weighted by
  difficulty and spread evenly to each deadline.
- **AI assistants** can read and edit through an
  [MCP](https://modelcontextprotocol.io) server at `/mcp`, with the
  same roles as people.
- **Built to be exposed to the internet:** OIDC sign-in with PKCE, roles
  from your identity provider's groups, a strict Content Security Policy,
  a read-only container running as an unprivileged user.

| Overview | Schedule |
|---|---|
| ![This week and the active projects](docs/images/overview.png) | ![A month of writing, by project](docs/images/schedule.png) |

## Run it

You need Docker and an OIDC identity provider. ReelLife is tested with
[PocketID](https://pocket-id.org); any provider that sends a `groups`
claim should work.

```bash
mkdir reellife && cd reellife
curl -fsSLO https://raw.githubusercontent.com/nhadler/reellife/main/deploy/docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/nhadler/reellife/main/deploy/.env.example -o .env
# Fill in .env: the secret key, your hostname and the OIDC client.
docker compose up -d
```

It listens on `127.0.0.1:8000`; put a TLS-terminating proxy in front
(Caddy, Traefik, Tailscale Serve). Images are published for
`linux/amd64` and `linux/arm64` at
[`ghcr.io/nhadler/reellife`](https://github.com/nhadler/reellife/pkgs/container/reellife),
tagged by version (`0.4.0`, `0.4`) and `latest`. Pin a release in `.env`
with `REELLIFE_IMAGE=ghcr.io/nhadler/reellife:0.4.0`.

[`deploy/README.md`](deploy/README.md) covers the identity provider
setup, every setting, backups, upgrades, and running without Docker
(systemd + uv).

## Develop

You need [uv](https://docs.astral.sh/uv/), [just](https://just.systems/)
and Node.js 22.

```bash
just setup    # tooling and git hooks
just dev      # http://127.0.0.1:8000, with a fake sign-in provider
just seed     # a demo project to click around in
just lint     # every lint, type and security gate CI runs
just cov      # tests, with 100% branch coverage
```

Django 5.2 renders the pages; HTMX and Alpine.js (its CSP build) make
them interactive without inline script, and AG Grid Community draws the
cue grid. Data is SQLite. The Django apps are `cues` (the domain:
projects, cues, versions, imports, exports, the schedule), `accounts`
(OIDC sign-in and roles) and `mcp_server` (the MCP endpoint); `reellife`
holds the settings. Timecodes are stored as integer frames at the
project's nominal rate, never as strings. Read
[CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

## Project status

ReelLife is in daily use by one composer and under active development;
see the [changelog](CHANGELOG.md). Until 1.0, minor versions may change
behaviour; upgrades migrate the database automatically and the changelog
says when anything needs your attention.

## Security

Please report vulnerabilities privately; see [SECURITY.md](SECURITY.md).

## License

ReelLife is free software: you can redistribute it and modify it under
the terms of the [GNU Affero General Public License](LICENSE), version 3
or later. If you run a modified copy for others, the AGPL asks you to
offer them its source; set `REELLIFE_SOURCE_URL` to point Help's Source
Code link at it. Third-party components and their licences are listed
in [NOTICE.md](NOTICE.md).
