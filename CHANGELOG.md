# Changelog

All notable changes to this project are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.4.0] - 2026-11-01

The first public release.

### Added

- **Projects** for films and episodes, with picture types (reels, acts,
  clips or long play), any frame rate including drop-frame, deadlines,
  days off, thumbnails and series.
- **Turnovers and picture versions**, with a conform page that shows
  which cues were written to older picture and shifts them by reel.
- **The cue grid:** reels on a timeline, coloured statuses, inline
  editing, filters, saved views (Standard, Spotting, Conform,
  Orchestration, Recording, Mixing, Viewer, Cue Sheet), a stats panel,
  bulk edits, cue tools (New Cues, Split, Merge, Renumber, Shift, Move,
  Copy, Delete) and undo for every change.
- **Cue details:** versions with their own notes, themes, sub status and
  the picture they were written to; department statuses; history with
  revert.
- **Imports** from Pro Tools Session Info as Text (clips or markers),
  Cubase and Nuendo, MIDI markers, and Excel or CSV, with a preview.
  Cue numbers must fit the project's Valid Cue Numbers.
- **Exports:** cue logs (Excel, CSV), PDF reports and wall charts,
  version notes, and RapidCue cue sheets with writers, publishers and
  shares.
- **Overview and Schedule:** this week across projects, and a daily
  writing plan weighted by difficulty and spread to each deadline.
- **Credits:** writers and publishers with default shares per project
  and series.
- **Activity and Trash** per project: undo any change, restore deleted
  cues.
- **Search** (Ctrl+K) across projects, cues and notes, and keyboard
  shortcuts throughout.
- **Help** pages, with the version and a link to the source.
- **MCP server** at `/mcp` so AI assistants can read and edit with the
  same roles as people.
- **Sign-in** through OIDC with PKCE (PocketID by default), roles from
  the provider's groups, and an emergency local admin.
- **Backup and restore** in one file, plus nightly database snapshots.
- **Deployment:** a hardened multi-architecture image at
  `ghcr.io/nhadler/reellife` with an SBOM and build provenance, Docker
  Compose, and a systemd + uv install.

[Unreleased]: https://github.com/nhadler/reellife/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/nhadler/reellife/releases/tag/v0.4.0
