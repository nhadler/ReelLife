# Exports

**Export Cues** (Ctrl+E) exports the cues shown (or the selected ones)
in the current view's columns:

- **Cue log** as Excel, CSV or PDF, optionally including music totals,
  progress and **Long Play Time** (below).
- **Wall chart**: a PDF card per cue, grouped by reel and coloured by
  status.
- **Version notes**: each cue's notes for its current version, or for
  every version.

**Copy JSON** (Ctrl+Shift+J) puts the selected cues' data on the
clipboard for Keyboard Maestro or SoundFlow macros.

The cue sheet has its own export (see *Credits and Cue Sheets*).

## Long Play Time

For reels, acts or clips, the cue log can give times on one continuous
timeline, as if the reels were joined: tick **Long Play Time**, choose
whether it **Starts at** 01:00:00:00 or 00:00:00:00, and type any
**Leader** before the first reel (e.g. `0:00:08:00`). Each reel follows
the ones before it by its current picture length (OUT minus IN), so a
cue 10 seconds into reel 2 lands at the start, plus the leader, plus
reel 1's length, plus 10 seconds.

IN, OUT, Seq. Start and the corresponding version fields are converted
to the joined timeline. Durations and offsets stay unchanged.

Cues under *No Reel Yet*, or on a reel without a current IN and OUT,
have blank positions in the export. Excel and PDF files list the reels
missing timecodes. Those reels contribute no length to the timeline.
Long-play projects don't offer this option because they already use one
timeline. Wall charts and version notes don't use it either.
