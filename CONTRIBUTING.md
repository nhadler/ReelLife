# Contributing to ReelTime

This page covers development setup and the requirements for a pull
request.

## Before you start

- **Bugs:** open an issue with steps to reproduce (the bug form asks
  for what's needed).
- **Features:** open an issue first and describe the problem it solves
  for a composer. Agree on the approach before writing code.
- **Security issues:** report them privately. See [SECURITY.md](SECURITY.md).

## Set up

You need [uv](https://docs.astral.sh/uv/), [just](https://just.systems/)
and Node.js 22.

```bash
just setup    # Python and JS tooling, plus the git hooks
just dev      # http://127.0.0.1:8000, with a fake sign-in provider
just seed     # a demo project: 3 reels, 2 turnovers, 40 cues
```

`just dev` starts a fake OIDC provider on port 8001 with a page to
choose who signs in (an editor, an admin, a viewer, or someone with no
access). Use `127.0.0.1`, not `localhost`, so the sign-in callback keeps
your session cookie.

## Project structure

The backend is organised into these packages:

- `cues`: Projects, cues, versions, imports, exports, and scheduling
- `accounts`: OIDC authentication and user roles
- `mcp_server`: MCP endpoint

The `reeltime` package contains the Django project settings, URLs,
middleware, and entry points.

## Make the change

- **Style:** the Google Python, JavaScript and HTML/CSS style guides.
  `just fmt` formats and fixes what it can.
- **Tests:** put them in `tests/small` (no database or
  network), `tests/medium` (Django and the database) or `tests/large`
  (the browser, the image, systemd). Coverage must stay at 100% branch
  for the whole app; timecode, importers, cue sheet exports and
  authorisation must never drop below it.
- **Security:** keep the strict CSP (no inline script or
  style), server-side role checks, `defusedxml` for XML, and formula
  escaping in spreadsheets. If one gets in your way, say so in the pull
  request rather than bypassing it.
- **Timecodes are integer frames** at the nominal rate; never store a
  timecode string. A cue is a moment in the picture, a version is one
  delivery.
- **UI text:** use Title Case for names and labels, sentence case for
  sentences. Put detailed explanations in Help, not on the page.
- Add a line to `CHANGELOG.md` under *Unreleased* for anything a user
  would notice.

## Check it

Run what CI runs before you push:

```bash
just lint     # ruff, pylint, mypy, bandit, migrations, deploy check, ESLint, Prettier
just cov      # small and medium tests with the coverage floors
just e2e      # browser tests with axe (first time: uv run playwright install chromium)
just test-container   # the image, Compose and systemd (needs Docker)
```

## Commits and pull requests

- Keep commits focused. Use an imperative subject ("Show form errors
  above the form") and explain why in the body.
- One topic per pull request. Fill in the template; link the issue.
- A maintainer reviews each change using Google's code review
  practices, including design, tests, naming, comments, and scope.

## Licence

ReelTime is licensed under the [GNU AGPL v3.0 or later](LICENSE). By
contributing you agree that your contribution is licensed the same way.
