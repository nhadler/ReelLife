# Exports

**Export Cues** (Ctrl+E) exports the cues shown (or the selected ones)
in the current view's columns:

- **Cue log** as Excel, CSV or PDF, with music totals and progress if
  you like, and optionally in **Long Play Time** (below).
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

IN, OUT, Seq. Start and the version's IN, OUT and Seq. Start move;
durations and offsets stay as they are. Cues that aren't on a reel with
an IN and OUT yet (under *No Reel Yet*, or on a reel no turnover has
timed) have no place on the timeline: their times are left blank, and
the Excel and PDF files say which reels those are. A reel without IN and
OUT takes no time on the timeline. Long-play projects are one timeline
already, so they don't offer it; the wall chart and version notes don't
use it.
