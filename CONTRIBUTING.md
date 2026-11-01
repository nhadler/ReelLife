# Contributing to ReelLife

Thanks for helping. ReelLife is a small project with a high bar: a
composer trusts it with deadlines and cue sheets that decide how they
get paid. This page covers how to get set up and what a change needs
before it's merged.

## Before you start

- **Bugs:** open an issue with steps to reproduce (the bug form asks
  for what's needed).
- **Features:** open an issue first and describe the problem it solves
  for a composer, so the approach can be agreed before you write code.
- **Security issues:** never in public. See [SECURITY.md](SECURITY.md).

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

## Make the change

- **Style:** the Google Python, JavaScript and HTML/CSS style guides.
  `just fmt` formats and fixes what it can.
- **Tests at the right size**, in `tests/small` (no database or
  network), `tests/medium` (Django and the database) or `tests/large`
  (the browser, the image, systemd). Coverage must stay at 100% branch
  for the whole app; timecode, importers, cue sheet exports and
  authorisation must never drop below it.
- **Security controls stay on.** Strict CSP (no inline script or
  style), roles checked on the server, `defusedxml` for XML, formula
  escaping in spreadsheets. If one gets in your way, say so in the pull
  request rather than working around it.
- **Timecodes are integer frames** at the nominal rate; never store a
  timecode string. A cue is a moment in the picture, a version is one
  delivery.
- **Words in the UI:** names and labels in Title Case, sentences in
  sentence case; no explanatory paragraphs on pages (Help has those).
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

- Small, focused commits. The subject says what changes for the user in
  the imperative ("Show form errors above the form"); the body says why.
- One topic per pull request. Fill in the template; link the issue.
- A maintainer reviews every change against Google's code review
  practices: design, tests, naming, comments, and whether it fits the
  plan.

## Licence

ReelLife is licensed under the [GNU AGPL v3.0 or later](LICENSE). By
contributing you agree that your contribution is licensed the same way.
