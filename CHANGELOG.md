# Changelog

All notable changes to this project are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Schedule On…** (the cue page's ⋯ menu, the grid's right-click menu,
  or a day's list on the Schedule): pin a cue to a day and the rest of
  the plan flows around it; a pin marks it on the calendar. Undoable.
- **Calendar Feed** (profile menu): a private link your calendar app
  subscribes to, one event a day listing its cues, the last 30 days and
  the next 90; revoke or replace it any time, or download an .ics once.
- **Log Written…** (a day's list on the Schedule, the grid's toolbar
  with one cue ticked, the cue page's ⋯ menu, the grid's right-click
  menu): how much of a cue you wrote on a
  day. The schedule counts only logged work as done and plans the rest;
  past days show what was planned beside what was written. Undoable.
- The Schedule keeps each day's plan, so past days show what was
  planned, faded. The summary starts "On track" when every deadline is
  met.
- MCP tools `schedule` ("what should I write today?"), `pin_cue` and
  `log_written`.
- **Projects at Once** (Settings, Schedule): work every project each
  day, each at its own pace to its own deadlines (In parallel, the
  default), or one at a time, earliest deadline first. With a daily
  maximum, projects asking for less than an even share keep all of it.

### Changed

- The Schedule's month fills the window instead of scrolling: weeks
  share the height, each day shows the cues that fit (titles on one
  line) and says how many more. Choosing a day swaps its list in
  without reloading the page.
- Runs on Django 6.1.
- Empty pages (Projects, Overview, Picture, Activity, Trash) show an
  icon, a short title and, where there's something to do, the button
  for it.
- Dialogs come in two widths; the Cue Sheet dialog's button says Export
  Cue Sheet, and Edit View has a × like the others.
- Import and Restore Backup drop zones show a file, not a picture.
- Days off on Your Days Off are hatched, as on the Schedule.
- A page that doesn't exist shows Page Not Found inside the app, with a
  way back to the Overview.
- Cues without a thumbnail show the project's initials in the grid.
- App Settings is one sheet: each section a row, its controls lined up
  on shared columns, so the page fits a laptop screen without
  scrolling. Viewers see it read-only.
- A project's work waits for its composition deadline's start date: the
  Schedule plans nothing for it before then, and Per Day counts the
  days from the start. Other projects have those days to themselves.
- New Project and every Project Settings page use the App Settings
  layout: each section's name in a left column, its fields beside it,
  and a rule between sections.
- Empty choices in every menu show a dash.
- Option labels are in Title Case throughout, and blank choices show a
  dash. Two to four choices are one segmented control; longer lists are
  menus. Explanations moved from dialogs into Help.
- Tall grid rows start every cell on the first line, and wrapped text
  stops at six lines with a fade.
- The project bar's menu is called More; the selection bar's actions
  are buttons; empty lists show one line and Add.
- On a phone the top bar's links fade at the edge, and the cue page
  skips its section index.
- Clarified Help text, UI descriptions, and contributor documentation.

### Fixed

- A project card's ⋯ menu was cut off at the card's edge.
- A project card's deadline label ("Composition Nov 22") was cut off.
- A fresh install no longer takes a needless snapshot before its first
  migration.
- Menus and the Filter panel no longer run past the bottom of the
  window.
- Delete Project stays disabled until the project's title is typed.

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
