# Security policy

ReelTime stores unreleased project data and collaborator details. Report
security issues privately so they can be fixed before disclosure.

## Reporting a vulnerability

Please **don't open a public issue**. Report it privately through
GitHub: the repository's **Security** tab → **Report a vulnerability**.
Include what you found, how to reproduce it, and what an attacker could
do with it.

You'll get an answer within a week. Once a fix is released, the
advisory is published with credit to you, unless you'd rather not be
named.

## Supported versions

Only the latest release gets security fixes. Run a tagged image
(`ghcr.io/nhadler/reeltime:<version>`) and update when a release notes a
security fix.

## What's in scope

Anything that lets someone read or change data they shouldn't, run
code, or get around sign-in or roles: the web app, the MCP endpoint at
`/mcp`, the Docker image and the systemd install.

Security controls include OIDC sign-in with PKCE and server-side role checks, a strict
Content Security Policy with no inline script, rate limits on writes,
`defusedxml` for XML imports, formula escaping in CSV and Excel exports,
a read-only container running as an unprivileged user, and weekly
dependency and vulnerability checks in CI.

Out of scope: the OIDC provider itself, the reverse proxy in front of
ReelTime, and attacks that need an admin account.
