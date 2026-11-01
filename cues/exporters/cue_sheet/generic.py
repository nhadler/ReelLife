"""The generic cue sheet CSV: one row per party per cue."""

import csv
import io

from cues import timecode
from cues.exporters.cue_log import csv_safe
from cues.exporters.cue_sheet.model import CueSheet

HEADERS = (
    "Seq. #",
    "Cue #",
    "Title",
    "Usage",
    "Time In",
    "Time Out",
    "Duration",
    "Role",
    "Name",
    "Writer or Publisher",
    "PRO",
    "IPI/CAE #",
    "Share %",
    "Artist",
    "Notes",
)


def _clock(seconds: int) -> str:
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}:{secs:02d}"


def to_csv(sheet: CueSheet) -> bytes:
    """The sheet as CSV, a cue with no parties as one row without them."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(HEADERS)

    def tc(frames: int | None) -> str:
        return (
            ""
            if frames is None
            else timecode.format_timecode(frames, sheet.rate)
        )

    for line in sheet.lines:
        cue = [
            str(line.sequence),
            line.number,
            line.title,
            line.usage,
            tc(line.tc_in),
            tc(line.tc_out),
            _clock(line.seconds),
        ]
        rest = [line.artist, line.notes]
        parties = [
            [
                p.role,
                p.name,
                p.kind.capitalize(),
                p.society,
                p.ipi,
                f"{p.share.normalize():f}",
            ]
            for p in line.parties
        ] or [[""] * 6]
        for party in parties:
            writer.writerow([csv_safe(v) for v in [*cue, *party, *rest]])
    # A byte order mark so Excel reads the file as UTF-8.
    return ("﻿" + out.getvalue()).encode("utf-8")
