"""The import dialog's steps on top of the importers.

The dialog posts the file with every step (preview, re-preview, import),
so nothing uploaded is kept on the server between requests. Each step
parses and plans again; a fingerprint of the preview travels with the
form so an import refuses to run if the project changed since the
preview was shown, rather than applying choices to a different list.
"""

import dataclasses
import enum
import hashlib
import json
import pathlib
from typing import Any

from cues import models
from cues.importers import apply
from cues.importers import base
from cues.importers import cubase
from cues.importers import merge
from cues.importers import midi
from cues.importers import protools
from cues.importers import tabular

MAX_BYTES = 10 * 1024 * 1024


class Kind(enum.StrEnum):
    """What the uploaded file is."""

    DETECT = "detect"
    PROTOOLS = "protools"
    CUBASE = "cubase"
    MIDI = "midi"
    SPREADSHEET = "spreadsheet"


_SPREADSHEET_SUFFIXES = frozenset({".csv", ".tsv", ".xlsx", ".xlsm"})


def detect(filename: str, data: bytes) -> Kind:
    """Guesses the file kind from its name, then its first bytes."""
    suffix = pathlib.PurePath(filename).suffix.lower()
    if suffix == ".txt":
        return Kind.PROTOOLS
    if suffix == ".xml":
        return Kind.CUBASE
    if suffix in {".mid", ".midi"} or data.startswith(b"MThd"):
        return Kind.MIDI
    if suffix in _SPREADSHEET_SUFFIXES or data.startswith(b"PK"):
        return Kind.SPREADSHEET
    head = data[:200].lstrip(b"\xef\xbb\xbf\xff\xfe\x00 \r\n\t").upper()
    if head.startswith(b"<"):
        return Kind.CUBASE
    if head.replace(b"\x00", b"").startswith(b"SESSION NAME"):
        return Kind.PROTOOLS
    return Kind.SPREADSHEET


@dataclasses.dataclass(frozen=True)
class Source:
    """How to read the file: the dialog's source options.

    Attributes:
        kind: The file kind (never DETECT).
        method: Pro Tools clips or markers.
        track_prefixes: Pro Tools track names to read clips from.
        start_frames: Cubase and MIDI: the timecode at DAW position 0.
        columns: Spreadsheet: column index -> field (None ignores it),
            overriding the automatic header matching.
    """

    kind: Kind
    method: protools.Method = protools.Method.CLIPS
    track_prefixes: tuple[str, ...] = protools.DEFAULT_TRACK_PREFIXES
    start_frames: int = 0
    columns: dict[int, str | None] = dataclasses.field(default_factory=dict)


def parse(
    project: models.Project, data: bytes, source: Source
) -> base.ParsedFile:
    """Reads the uploaded file with the matching importer.

    Raises:
        base.ImportFormatError: The file can't be read as that kind.
    """
    if source.kind == Kind.PROTOOLS:
        return protools.parse(
            protools.decode(data),
            method=source.method,
            track_prefixes=source.track_prefixes,
        )
    if source.kind == Kind.CUBASE:
        return cubase.parse(
            data, rate=project.rate, start_frames=source.start_frames
        )
    if source.kind == Kind.MIDI:
        return midi.parse(
            data, rate=project.rate, start_frames=source.start_frames
        )
    return tabular.parse(data, rate=project.rate, mapping=source.columns)


def default_start(project: models.Project) -> int:
    """DAW start: the last one used, else the first reel's IN."""
    if project.daw_start is not None:
        return project.daw_start
    first = (
        models.PictureVersion.objects.filter(
            picture__project=project, tc_in__isnull=False
        )
        .order_by("picture__order", "picture__name", "turnover__received_date")
        .values_list("tc_in", flat=True)
        .first()
    )
    return first or 0


def plan(
    project: models.Project, parsed: base.ParsedFile, options: merge.Options
) -> merge.Preview:
    """merge.plan, plus what applying would substitute in new cues' fields."""
    preview = merge.plan(project, parsed, options)
    preview.warnings.extend(apply.field_warnings(project, preview.changes))
    return preview


def fingerprint(preview: merge.Preview) -> str:
    """A digest of what the preview proposes, to check before applying.

    It covers the existing cues' timings and titles as the preview showed
    them, so a change someone makes in between (an assistant fixing a
    cue's IN) makes the stale preview's confirmation fail instead of
    being overwritten unseen.
    """
    parts: list[list[Any]] = [
        [
            change.action,
            change.row.number,
            change.row.tc_in,
            change.row.tc_out,
            change.row.seq_start,
            change.cue_id,
            change.old_in,
            change.old_out,
            change.old_title,
            change.new_title,
            change.picture_id,
        ]
        for change in preview.changes
    ]
    parts.append(sorted(cue.cue_id for cue in preview.missing))
    payload = json.dumps(parts, default=str).encode()
    return hashlib.sha256(payload).hexdigest()
