"""What every importer produces, and cue-name parsing they share."""

from collections.abc import Mapping
import dataclasses
import re


class ImportFormatError(ValueError):
    """The file can't be imported; the message tells the user why."""


@dataclasses.dataclass(frozen=True)
class ImportRow:
    """One cue as read from a file, before matching against a project.

    Attributes:
        label: The name as written in the file, e.g. '1m01 Main Title'.
        number: Cue number parsed from the label, e.g. '1m01'.
        title: Title parsed from the label; may be empty.
        tc_in: IN in frames, or None if missing or unreadable.
        tc_out: OUT in frames, or None.
        seq_start: Sequencer start in frames, or None.
        line: Line (or spreadsheet row) it came from, for the preview.
        fields: Extra spreadsheet columns by our field name, e.g.
            {'status': 'Composed'}; empty for DAW files.
    """

    label: str
    number: str
    title: str
    tc_in: int | None
    tc_out: int | None
    seq_start: int | None = None
    line: int = 0
    fields: Mapping[str, str] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class Column:
    """A spreadsheet column and the field it maps to (None = ignored)."""

    index: int
    header: str
    field: str | None


@dataclasses.dataclass(frozen=True)
class ParsedFile:
    """An importer's output.

    Attributes:
        source: What was read, e.g. 'Pro Tools clips'.
        rows: Cues in file order.
        warnings: Per-row problems that didn't stop the import.
        frame_rate: The rate the file states (Pro Tools does), as a
            project frame rate label, else None.
        drop_frame: Whether the file states drop-frame, else None.
        columns: For spreadsheets, every column and how it was mapped, so
            the preview can offer to map the unrecognised ones.
    """

    source: str
    rows: list[ImportRow]
    warnings: list[str] = dataclasses.field(default_factory=list)
    frame_rate: str | None = None
    drop_frame: bool | None = None
    columns: list[Column] = dataclasses.field(default_factory=list)


_NAME = re.compile(r"^\s*(?P<number>\S+)\s*(?P<title>.*?)\s*$", re.DOTALL)
# '1m01 Main Title IN', '1m01 OUT', and the composer's '1m01 MX IN'.
_MARKER_ROLE = re.compile(
    r"^(?P<rest>.*?)\s+(?:MX\s+)?(?P<role>IN|OUT)\s*$", re.I
)


def split_name(label: str) -> tuple[str, str]:
    """Splits '1m01 Main Title' into ('1m01', 'Main Title').

    The first whitespace-separated token is the cue number; whether it
    is a *valid* number is the project's cue number pattern's call.
    """
    match = _NAME.match(label)
    if match is None:
        return "", ""
    return match["number"], match["title"]


def marker_role(label: str) -> tuple[str | None, str]:
    """Reads a marker name like '1m01 Main Title IN' or '1m01 MX OUT'.

    Returns:
        ('in' or 'out', the label without the suffix), or (None, label)
        for markers that aren't cue boundaries.
    """
    match = _MARKER_ROLE.match(label)
    if match is None:
        return None, label.strip()
    return match["role"].lower(), match["rest"].strip()
