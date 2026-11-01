"""Excel and CSV cue lists.

The first sheet (or the CSV) needs a cue number column. The header row is
found automatically in the first rows, headers map to fields through the
synonyms below, and anything unrecognised is reported so the user can map
it. OUT is computed from DUR when only a duration is given. This also
covers migrating from a spreadsheet or another app's cue log.

Excel files are opened read-only after checking their uncompressed size,
so a zip bomb is refused before openpyxl touches it, and read within a
column and cell budget, so a sheet that claims to be enormous is too.
"""

from collections.abc import Mapping
import csv
import datetime
import io
import re
from typing import Any
import zipfile

import openpyxl

from cues import timecode
from cues.importers import base

# Our field -> header synonyms (compared lowercased, spaces collapsed).
SYNONYMS: dict[str, tuple[str, ...]] = {
    "number": ("cue #", "cue", "cue number", "cue no", "cue no.", "#"),
    "title": ("title", "name", "cue title"),
    "tc_in": ("in", "start", "tc in", "timecode in"),
    "tc_out": ("out", "end", "tc out", "timecode out"),
    "duration": ("dur", "duration", "length"),
    "seq_start": ("seq start", "seq. start", "sequencer start"),
    "status": ("status",),
    "notes": ("notes",),
    "spotting_notes": ("spotting notes",),
    "version": ("version", "cue version"),
    "usage": ("usage",),
    "cue_type": ("cue type", "type"),
    # Common cue log columns, for bringing projects over.
    "temp": ("temp",),
    "key": ("key",),
    "bpm": ("bpm", "tempo"),
    "meter": ("meter", "time signature"),
    "in_note": ("in note", "in notes", "mx in"),
    "out_note": ("out note", "out notes", "mx out"),
    "scene": ("scene",),
    "scene_description": ("scene description",),
    "priority": ("priority",),
    "difficulty": ("difficulty",),
    "artist": ("artist",),
    "cue_sheet_title": ("cue sheet title", "registered title"),
}
FIELDS = tuple(SYNONYMS)
# Fields passed through to ImportRow.fields for new cues.
EXTRA_FIELDS = (
    "status",
    "notes",
    "spotting_notes",
    "version",
    "usage",
    "cue_type",
    "temp",
    "key",
    "bpm",
    "meter",
    "in_note",
    "out_note",
    "scene",
    "scene_description",
    "priority",
    "difficulty",
    "artist",
    "cue_sheet_title",
)

_HEADER_SEARCH_ROWS = 10
_MAX_UNCOMPRESSED = 50 * 1024 * 1024
_MAX_ZIP_ENTRIES = 2_000
_MAX_ROWS = 20_000
# A sheet's declared size can be far bigger than its data (A1:XFD20001
# is 327 million cells, all padded out by openpyxl), so only this many
# columns are read, and at most this many cells up to each row's last
# filled one.
_MAX_COLUMNS = 256
_MAX_CELLS = 1_000_000


def _normalise(header: str) -> str:
    return re.sub(r"\s+", " ", header.strip().lower())


_LOOKUP = {
    synonym: field
    for field, synonyms in SYNONYMS.items()
    for synonym in synonyms
}


def _cell(value: object) -> str:
    """Spreadsheet cell -> text, keeping cue numbers like 101 intact."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime.time):
        # A duration typed as h:mm:ss in Excel.
        return f"{value.hour}:{value.minute:02d}:{value.second:02d}:00"
    return str(value).strip()


def _xlsx_rows(data: bytes) -> list[list[str]]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise base.ImportFormatError("The Excel file is damaged.") from exc
    if (
        len(entries) > _MAX_ZIP_ENTRIES
        or sum(entry.file_size for entry in entries) > _MAX_UNCOMPRESSED
    ):
        raise base.ImportFormatError(
            "The Excel file expands to more than 50 MB; refusing to open it."
        )
    try:
        workbook = openpyxl.load_workbook(
            io.BytesIO(data), read_only=True, data_only=True
        )
    except Exception as exc:  # openpyxl raises many types for bad files
        raise base.ImportFormatError(
            "The file isn't a readable Excel workbook (.xlsx)."
        ) from exc
    try:
        return _sheet_rows(workbook.worksheets[0])
    finally:
        workbook.close()


def _sheet_rows(sheet: Any) -> list[list[str]]:
    """The sheet's rows as text, within the column and cell limits.

    Columns after the _MAX_COLUMNS-th are ignored, and so are empty cells
    at the end of each row.

    Raises:
        base.ImportFormatError: More than _MAX_CELLS cells to read.
    """
    width = min(sheet.max_column or _MAX_COLUMNS, _MAX_COLUMNS)
    rows = []
    cells = 0
    for row in sheet.iter_rows(max_col=width, values_only=True):
        end = len(row)
        while end and row[end - 1] is None:
            end -= 1
        cells += end
        if cells > _MAX_CELLS:
            raise base.ImportFormatError(
                f"The sheet has more than {_MAX_CELLS:,} filled cells, too "
                "many for a cue list. Delete the rows and columns it "
                "doesn't need, or save just the cues as a new workbook."
            )
        rows.append([_cell(value) for value in row[:end]])
        if len(rows) > _MAX_ROWS:
            break
    return rows


def _csv_table(text: str, delimiter: str) -> list[list[str]]:
    rows = []
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        for row in reader:
            rows.append([cell.strip() for cell in row])
            if len(rows) > _MAX_ROWS:
                break
    except csv.Error as exc:
        raise base.ImportFormatError(f"Not a readable CSV file: {exc}") from exc
    return rows


def _csv_rows(data: bytes) -> list[list[str]]:
    """Reads a CSV, picking the delimiter that yields a cue header.

    Title lines above the header can fool csv.Sniffer, so each common
    delimiter is tried and the first that finds a header wins.
    """
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    candidates = [",", ";", "\t"]
    try:
        sniffed = csv.Sniffer().sniff(text[:4096], delimiters=",;\t").delimiter
        candidates.insert(0, sniffed)
    except csv.Error:
        pass
    table: list[list[str]] = []
    for delimiter in candidates:
        table = _csv_table(text, delimiter)
        if _header_index(table) is not None:
            return table
    return table


def read_table(data: bytes) -> list[list[str]]:
    """Reads the first sheet of an .xlsx, or a CSV, as rows of text.

    Raises:
        base.ImportFormatError: A damaged or oversized workbook.
    """
    if data.startswith(b"PK\x03\x04"):
        return _xlsx_rows(data)
    return _csv_rows(data)


def _header_index(table: list[list[str]]) -> int | None:
    for index, row in enumerate(table[:_HEADER_SEARCH_ROWS]):
        if any(_LOOKUP.get(_normalise(cell)) == "number" for cell in row):
            return index
    return None


def _find_header(table: list[list[str]]) -> int:
    index = _header_index(table)
    if index is not None:
        return index
    raise base.ImportFormatError(
        "No cue number column found. The first rows need a header such as "
        "'Cue #', 'Cue' or 'Cue Number'."
    )


def _columns(
    header: list[str], overrides: Mapping[int, str | None]
) -> list[base.Column]:
    columns = []
    taken: set[str] = set()
    for index, title in enumerate(header):
        if index in overrides:
            field = overrides[index]
        else:
            field = _LOOKUP.get(_normalise(title))
            if field in taken:
                field = None  # first matching column wins
        if field is not None:
            if field not in SYNONYMS:
                raise ValueError(f"unknown field {field!r}")
            taken.add(field)
        columns.append(base.Column(index, title, field))
    return columns


class _RowReader:
    """Reads one spreadsheet row into an ImportRow."""

    def __init__(
        self,
        cells: list[str],
        by_field: dict[str, int],
        rate: timecode.Rate,
        line: int,
        warnings: list[str],
    ) -> None:
        self.cells = cells
        self.by_field = by_field
        self.rate = rate
        self.line = line
        self.warnings = warnings

    def text(self, field: str) -> str:
        index = self.by_field.get(field)
        if index is None or index >= len(self.cells):
            return ""
        return self.cells[index]

    def frames(self, field: str) -> int | None:
        text = self.text(field)
        if not text:
            return None
        try:
            if field == "duration":
                return timecode.parse_duration(text, self.rate)
            return timecode.parse(text, self.rate)
        except timecode.TimecodeError as exc:
            label = SYNONYMS[field][0].upper()
            self.warnings.append(f"Row {self.line}: {label} {exc}")
            return None

    def row(self) -> base.ImportRow:
        number, title = self.text("number"), self.text("title")
        tc_in, tc_out = self.frames("tc_in"), self.frames("tc_out")
        duration = self.frames("duration")
        if tc_out is None and tc_in is not None and duration is not None:
            tc_out = tc_in + duration
        return base.ImportRow(
            label=f"{number} {title}".strip(),
            number=number,
            title=title,
            tc_in=tc_in,
            tc_out=tc_out,
            seq_start=self.frames("seq_start"),
            line=self.line,
            fields={f: self.text(f) for f in EXTRA_FIELDS if self.text(f)},
        )


def parse(
    data: bytes,
    *,
    rate: timecode.Rate,
    mapping: Mapping[int, str | None] | None = None,
) -> base.ParsedFile:
    """Reads a cue list from an .xlsx or CSV file.

    Args:
        data: The file's bytes.
        rate: The project's frame rate, for timecodes and durations.
        mapping: Column index -> field (or None to ignore), overriding the
            automatic header matching; from the preview's dropdowns.

    Returns:
        One row per non-blank line after the header, with each column's
        mapping in `columns`.

    Raises:
        base.ImportFormatError: No cue number column, or a bad workbook.
        ValueError: `mapping` names an unknown field.
    """
    table = read_table(data)
    header_index = _find_header(table)
    columns = _columns(table[header_index], mapping or {})
    by_field = {c.field: c.index for c in columns if c.field is not None}
    warnings: list[str] = []
    rows = []
    # Spreadsheet row numbers are 1-based; data starts after the header.
    for line, cells in enumerate(
        table[header_index + 1 :], start=header_index + 2
    ):
        if any(cells):
            reader = _RowReader(cells, by_field, rate, line, warnings)
            rows.append(reader.row())
    return base.ParsedFile(
        source="Spreadsheet", rows=rows, warnings=warnings, columns=columns
    )
