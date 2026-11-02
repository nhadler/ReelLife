# Importing

**Import** in the grid's toolbar (Ctrl+I) reads cues from:

- **Pro Tools session text** (File, Export, Session Info as Text, with
  track EDLs): cues are either **clips on cue tracks** (tracks whose
  names start with *Cues*, or the names you give, e.g. `Cues, MX`) or
  **IN and OUT markers**.
- **Cubase or Nuendo XML** (the marker track exported as XML). Give the
  **Project start** if the session doesn't start at the first reel's IN.
- **MIDI file markers**. A file with its own SMPTE offset uses that.
- **Excel or CSV**: a row per cue. ReelLife matches the columns by name
  (Cue, Title, Start or IN, End or OUT, Status, Notes, BPM, Cue Sheet
  Title and more) and shows how it read them.

Leave **Import from** on *Detect from the File* unless it guesses wrong.

**New Cues Go To** the reel their IN falls in (the act or clip, as your
picture type has it), or to the one you pick; a cue whose IN is in no
reel waits under *No Reel Yet*.

## Clip and Marker Names

- *Cue # and Title*: `1m01 Main Title` (the usual).
- *Cue # Only*: `1m01`.
- *Titles, Numbered in Order*: from the **First cue #** you give.

New cues whose numbers don't fit the project's **Valid Cue Numbers**
are left out and listed in the preview; a clip for a cue you already
have updates it whatever its number.

Clips sharing a cue # become one cue (earliest IN, latest OUT). If they
leave a second or more of silence between them, the preview warns you:
they may be two uses that need their own numbers.

## The Preview

Nothing changes until you import. The preview lists each cue as
**New**, **Update** (timecodes, or titles if you tick **Title**) or
**Same**, with old and new IN and OUT. Untick rows to leave them out.
It also warns about anything it will substitute (an unknown status
becomes your default) and cues that move to another reel. Choose what
to do with **cues not in the file**: leave them, make them inactive, or
set them to Omit.

If someone changes the project while you look at the preview, ReelLife
shows a fresh preview instead of importing over their change. An import
is one change: Undo takes all of it back.
