# Fixtures

`third_party/ptulsconv/` holds **real Pro Tools exports** from the open-source ptulsconv project (MIT licence, see the LICENSE file there). They're ADR/spotting sessions, not music cue sessions, so use them to test the parser itself: headers, every section type, stereo channel duplicates, padded timecodes, drop-frame `;`, the newer marker columns (`Test for ptulsconv.txt`) and a min:sec export that must be rejected (`Robin Hood SpottingMS.txt`).

The other files here are **written by hand** to exercise cue-specific rules. The Pro Tools ones follow the verified format. Use them to start the importer tests, then add real exports to `fixtures/real/` and make the tests pass against those.

| File | Tests |
|---|---|
| `protools_clips_23976.txt` | Pro Tools clip method: track named `Cues ...`; two clips for `1m03` must merge to earliest IN / latest OUT; `Dialogue` track ignored; 23.976 header |
| `protools_markers_2997df.txt` | Pro Tools marker method: `IN` / `OUT` pairs, OUT without title, `s` suffix cue, unrelated `FFOA` marker ignored, 29.97 DF timecodes with `;` |
| `cubase_marker_track_linear.xml` | Cubase/Nuendo cycle markers; position marker `FFOA` ignored. Structure is a guess, read the comment at the top |
| `midi_markers_25.mid` | MIDI markers: SMPTE offset 01:00:00:00 at 25 fps, 120 bpm then 60 bpm from 20 s, IN/OUT pairs, a bare `1m02`, a `cue_marker` event, an OUT with no IN. Made with mido; the resulting timecodes are in its golden JSON |
| `cue_list.csv` | header mapping, leading space trimming, OUT computed from DUR, invalid cue number row excluded, mixed `1:` / `01:` hour format |

Expected results for `protools_clips_23976.txt` at 23.976:

| Cue | Title | IN frames | OUT frames |
|---|---|---|---|
| 1m01 | Main Title | 86,640 | 88,572 |
| 1m02 | The Chase | 90,720 | 93,896 |
| 1m03 | Aftermath | 95,079 | 98,400 |
