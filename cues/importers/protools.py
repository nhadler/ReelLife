"""Pro Tools 'Session Info as Text'.

Two methods:

- **Clips**: each clip on a track whose name starts with a prefix
  (default `Cues`) is a cue named `<cue #> <title>`.
- **Markers**: a cue starts at `<cue #> <title> IN` (or `MX IN`), or at
  a marker that is just a cue number and title (`1m04`), and ends at
  its `<cue #> OUT` (or `MX OUT`). A cue with no OUT ends at the next
  marker of any kind (Nick, Oct 31, from the composer's real session).
  Markers without a cue number (`EC`, `OUT`) start no cue.

The format was checked against real exports (fixtures/third_party): tab-
separated sections under spaced-capital titles, right-aligned timecodes
with ';' for drop-frame, stereo tracks listing each clip once per channel,
and newer marker tables with extra columns. Sections are found by their
shape, columns by their header row, so a translated export still parses.
"""

import dataclasses
import enum
import re

from cues import timecode
from cues.importers import base
from cues.importers import markers


class Method(enum.StrEnum):
    """Where cues come from in the session."""

    CLIPS = "clips"
    MARKERS = "markers"


DEFAULT_TRACK_PREFIXES = ("Cues",)

_RATE = re.compile(
    r"^(?P<rate>60|59\.94|30|29\.97|25|24|23\.976)(?P<drop> Drop)? Frame$"
)
# 'T R A C K  L I S T I N G': single capitals separated by spaces.
_SECTION = re.compile(r"^[A-Z-](?: {1,2}[A-Z-])+$")
_HEADER = re.compile(r"^(?P<key>[^\t]+?):\t?(?P<value>.*)$")
_TIMECODE_SHAPE = re.compile(r"^\d{1,2}:\d{2}:\d{2}[:;.]\d{2}(?:\.\d{1,2})?$")

_TRACK_SECTION = "TRACKLISTING"
_MARKER_SECTION = "MARKERSLISTING"


@dataclasses.dataclass(frozen=True)
class _Line:
    number: int  # 1-based, for the preview
    text: str


def decode(data: bytes) -> str:
    """Decodes an export: UTF-8 (with or without BOM), else Mac Roman."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("mac_roman")


def _header(lines: list[_Line]) -> dict[str, str]:
    header = {}
    for line in lines:
        if _SECTION.match(line.text.strip()):
            break
        match = _HEADER.match(line.text)
        if match:
            header[match["key"].strip().upper()] = match["value"].strip()
    return header


def _rate(header: dict[str, str]) -> tuple[str, bool, timecode.Rate]:
    stated = header.get("TIMECODE FORMAT", "")
    match = _RATE.match(stated)
    if match is None:
        raise base.ImportFormatError(
            "This doesn't look like a Pro Tools 'Session Info as Text' "
            f"export with a timecode format (found {stated!r})."
        )
    label, drop = match["rate"], match["drop"] is not None
    try:
        rate = timecode.rate_for(label, drop_frame=drop)
    except ValueError as exc:
        raise base.ImportFormatError(str(exc)) from exc
    return label, drop, rate


def _sections(lines: list[_Line]) -> dict[str, list[_Line]]:
    sections: dict[str, list[_Line]] = {}
    current: list[_Line] | None = None
    for line in lines:
        title = line.text.strip()
        if _SECTION.match(title):
            current = sections.setdefault(title.replace(" ", ""), [])
        elif current is not None:
            current.append(line)
    return sections


def _columns(header_line: str) -> dict[str, int]:
    return {
        name.strip().upper(): index
        for index, name in enumerate(header_line.split("\t"))
    }


def _field(
    cells: list[str], columns: dict[str, int], name: str, fallback: int
) -> str:
    index = columns.get(name, fallback)
    return cells[index].strip() if index < len(cells) else ""


class _Timecodes:
    """Parses a file's timecodes, refusing non-timecode exports."""

    def __init__(self, rate: timecode.Rate) -> None:
        self.rate = rate
        self.warnings: list[str] = []

    def require_timecode(self, text: str, line: int, what: str) -> None:
        """Refuses min:sec, samples and other non-timecode exports.

        Raises:
            base.ImportFormatError: The value isn't shaped like timecode.
        """
        value = text.strip()
        if value and not _TIMECODE_SHAPE.match(value):
            raise base.ImportFormatError(
                f"Line {line}: {what} is {value!r}, not a timecode. In Pro "
                "Tools, export Session Info as Text again with Time Format "
                "set to Timecode."
            )

    def parse(self, text: str, line: int, what: str) -> int | None:
        value = text.strip()
        self.require_timecode(value, line, what)
        if not value:
            return None
        try:
            return timecode.parse(value, self.rate)
        except timecode.TimecodeError as exc:
            self.warnings.append(f"Line {line}: {what} {exc}")
            return None


def _clip_rows(
    section: list[_Line], prefixes: tuple[str, ...], times: _Timecodes
) -> list[base.ImportRow]:
    wanted = tuple(prefix.casefold() for prefix in prefixes)
    rows = []
    track = ""
    columns: dict[str, int] | None = None
    for line in section:
        text = line.text
        if text.upper().startswith("TRACK NAME:"):
            track = text.split(":", 1)[1].strip()
            columns = None
            continue
        if text.upper().startswith("CHANNEL"):
            columns = _columns(text)
            continue
        cells = text.split("\t")
        if columns is None or not cells[0].strip().isdigit():
            continue
        # Checked on every track, so a min:sec export is refused even when
        # no track matches.
        times.require_timecode(
            _field(cells, columns, "START TIME", 3), line.number, "start"
        )
        if not track.casefold().startswith(wanted):
            continue
        # Stereo and multichannel tracks list each clip per channel.
        if cells[0].strip() != "1":
            continue
        label = _field(cells, columns, "CLIP NAME", 2)
        number, title = base.split_name(label)
        rows.append(
            base.ImportRow(
                label=label,
                number=number,
                title=title,
                tc_in=times.parse(
                    _field(cells, columns, "START TIME", 3),
                    line.number,
                    "start",
                ),
                tc_out=times.parse(
                    _field(cells, columns, "END TIME", 4), line.number, "end"
                ),
                line=line.number,
            )
        )
    return rows


def _markers(section: list[_Line], times: _Timecodes) -> list[markers.Marker]:
    """The section's markers."""
    columns: dict[str, int] | None = None
    found = []
    for line in section:
        cells = line.text.split("\t")
        first = cells[0].strip()
        if first == "#":
            columns = _columns(line.text)
            continue
        if columns is None or not first.isdigit():
            continue
        location = _field(cells, columns, "LOCATION", 1)
        times.require_timecode(location, line.number, "location")
        name = _field(cells, columns, "NAME", 4)
        if markers.Marker.named(line.number, 0, name).starts_nothing():
            # FFOA and the like only end cues: a bad time isn't worth a
            # warning, it just ends nothing.
            try:
                at: int | None = timecode.parse(location.strip(), times.rate)
            except timecode.TimecodeError:
                continue
        else:
            at = times.parse(location, line.number, "location")
        if at is not None:
            found.append(markers.Marker.named(line.number, at, name))
    return found


def parse(
    text: str,
    *,
    method: Method,
    track_prefixes: tuple[str, ...] = DEFAULT_TRACK_PREFIXES,
) -> base.ParsedFile:
    """Reads cues from a Pro Tools session text export.

    Args:
        text: The decoded export (see `decode`).
        method: Clips on cue tracks, or cue markers.
        track_prefixes: For the clips method, track names to read.

    Returns:
        Rows in file order, with the session's frame rate.

    Raises:
        base.ImportFormatError: Not a session text export, a time format
            other than timecode, or the needed section is missing.
    """
    lines = [
        _Line(number, line.rstrip("\r\n"))
        for number, line in enumerate(text.splitlines(), start=1)
    ]
    label, drop, rate = _rate(_header(lines))
    sections = _sections(lines)
    times = _Timecodes(rate)
    if method == Method.CLIPS:
        section = sections.get(_TRACK_SECTION)
        if section is None:
            raise base.ImportFormatError(
                "The export has no track listing. Tick 'Include Track EDLs' "
                "when exporting Session Info as Text."
            )
        rows = _clip_rows(section, track_prefixes, times)
        source = "Pro Tools clips"
    else:
        section = sections.get(_MARKER_SECTION)
        if section is None:
            raise base.ImportFormatError(
                "The export has no markers. Tick 'Include Markers' when "
                "exporting Session Info as Text."
            )
        rows = markers.cue_rows(_markers(section, times), times.warnings)
        source = "Pro Tools markers"
    return base.ParsedFile(
        source=source,
        rows=rows,
        warnings=times.warnings,
        frame_rate=label,
        drop_frame=drop,
    )
