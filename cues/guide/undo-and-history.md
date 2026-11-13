# Undo and History

Nearly every change in ReelTime is one **change set**: a cell edit, a
tool run, an import, a turnover, a conform step, a save of a settings
page or of Credits.

- **Undo** (Ctrl+Z, or Tools, Undo Last Change) takes back your own
  latest change in the project, then the one before, and so on.
- A cue's page lists its **History**: each change, who made it and
  when, and what changed. **Revert** undoes that change when nothing
  later depends on it; otherwise undo the later change first.
- Changes made by an AI assistant say so ("via MCP").

## Activity

**Activity** (Settings menu, Project) lists every change to the
project, newest first, with the time, author, action and cue count.
**Undo** takes back any of them, not just your latest, as long as
nothing it changed was changed again afterwards; such an entry says
**Changed Since**, so undo the later change first. An undo can itself
be taken back with **Redo**. Viewers see the list without the buttons.

Undo won't remove a status, sub status, theme, instrument, department
status or writer still in use. It also won't restore an entry if its
name or cue number is now taken. The error identifies the conflict.

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
is also one change set. Undo and Activity restore both the lists and
any affected cues. The **Credits** page has its own **Undo Last Change**
and Recent Changes list.

Not undoable: views, the General, Picture and Deadlines settings pages,
and App Settings. Admins also have nightly backups (see *Settings and
Roles*).
