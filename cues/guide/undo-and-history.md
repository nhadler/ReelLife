# Undo and History

Nearly every change in ReelLife is one **change set**: a cell edit, a
tool run, an import, a turnover, a conform step, a save of a settings
page or of Credits.

- **Undo** (Ctrl+Z, or Tools, Undo Last Change) takes back your own
  latest change in the project, then the one before, and so on.
- A cue's page lists its **History**: each change, who made it and
  when, what it changed. **Revert** undoes that one change when nothing
  later depends on it; otherwise undo the later change first.
- Changes made by an AI assistant say so ("via MCP").

## Activity

**Activity** (Settings menu, Project) lists every change to the
project, newest first: when, who, what and how many cues it touched.
**Undo** takes back any of them, not just your latest, as long as
nothing it changed was changed again afterwards; such an entry says
**Changed Since**, so undo the later change first. An undo can itself
be taken back with **Redo**. Viewers see the list without the buttons.

Undo also refuses rather than leave things broken: it won't remove a
status, sub status, theme, instrument, department status or writer
that cues use now, or put back something whose name or cue number is
taken since, and it says which.

## Trash

**Trash** (Settings menu, Project) lists the cues deleted from the
project, with what deleted them and when. **Restore** brings a cue back
with its versions, credits, themes and instrumentation as they were,
even when the change that deleted it can no longer be undone. If
another cue has its number now, Trash offers to restore it with an
"r" added (1m05r). Deleted turnovers aren't in the Trash; undo their
deletion from Activity.

## Lists and Credits

Saving Project Settings' Statuses, Lists, Departments or Cue Sheet page
is one change set too, so Undo and Activity take it back, renamed cues
and all. The **Credits** page has its own **Undo Last Change** for your
latest change there, and lists its recent changes.

Not undoable: views, the General, Picture and Deadlines settings pages,
and App Settings. Admins also have nightly backups (see *Settings and
Roles*).
