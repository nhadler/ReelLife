"""The RapidCue cue sheet: ASCAP and BMI's 2016 Excel template, filled in.

The official template ships next to this module (the maintainer keeps
the file as downloaded in private/reference/rapidcue/) and is filled
cell by cell, so its lists, table and formulas stay as RapidCue
expects. A cue takes one row per party: its sequence, title, usage,
times and duration on the first, then each party's role, name, IPI, PRO
and share. Text goes in as text, never as a formula.
"""

import datetime
import io
import pathlib
import re
from typing import Any
import warnings

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

from cues import timecode
from cues.exporters.cue_sheet.model import CueSheet
from cues.exporters.cue_sheet.model import Line
from cues.exporters.cue_sheet.model import Party

TEMPLATE = pathlib.Path(__file__).with_name("rapidcue_template.xlsx")
FIRST_ROW = 20
LAST_TEMPLATE_ROW = 999
# The template's three roles: writers compose, arrange or publish.
ROLES = {
    "C": "Composer",
    "A": "Composer",
    "CA": "Composer",
    "AD": "Composer",
    "AR": "Arranger",
    "E": "Publisher",
    "SE": "Publisher",
    "AM": "Publisher",
}


def _put(sheet: Worksheet, ref: str, value: Any) -> None:
    """Writes a value; text stays text, even if it starts with '='."""
    if value in (None, ""):
        return
    cell = sheet[ref]
    cell.value = value
    if isinstance(value, str):
        cell.data_type = "s"


def _hms(
    frames: int | None, rate: timecode.Rate
) -> tuple[int, int, int] | None:
    if frames is None:
        return None
    h, m, s, _ = (
        int(p)
        for p in re.split(r"[:;]", timecode.format_timecode(frames, rate))
    )
    return h, m, s


def _header(sheet: Worksheet, cue_sheet: CueSheet) -> None:
    header = cue_sheet.header
    minutes, seconds = divmod(header.program_seconds, 60)
    for ref, value in {
        "D4": "Revision" if header.revision else "Original",
        "D5": header.prepared_on,
        "D8": header.release_date,
        "D9": header.category,
        "D10": header.version,
        "D11": header.network or header.distributor,
        "D13": minutes,
        "F13": seconds,
        "N4": header.title,
        "N5": header.aka,
        "N6": header.episode_title,
        "N8": header.episode_number,
        "N9": header.production_number,
        "N11": header.company,
        "N12": header.company_address,
        "N13": header.preparer,
        "N14": header.preparer_email,
    }.items():
        _put(sheet, ref, value)


def _cue(sheet: Worksheet, row: int, line: Line, rate: timecode.Rate) -> None:
    _put(sheet, f"A{row}", line.sequence)
    _put(sheet, f"B{row}", line.title)
    _put(sheet, f"C{row}", line.usage)
    for columns, frames in (("DEF", line.tc_in), ("GHI", line.tc_out)):
        parts = _hms(frames, rate)
        if parts:
            for column, value in zip(columns, parts, strict=True):
                sheet[f"{column}{row}"].value = value
    minutes, seconds = divmod(line.seconds, 60)
    sheet[f"J{row}"].value = minutes  # our rounding, not the template's
    sheet[f"K{row}"].value = seconds


def to_xlsx(cue_sheet: CueSheet) -> bytes:
    """The filled template."""
    with warnings.catch_warnings():
        # The template's print area is a formula openpyxl can't keep.
        warnings.simplefilter("ignore", UserWarning)
        book = openpyxl.load_workbook(TEMPLATE)
    sheet = book["Template"]
    _header(sheet, cue_sheet)
    row = FIRST_ROW
    for line in cue_sheet.lines:
        _cue(sheet, row, line, cue_sheet.rate)
        parties: list[Party | None] = [*line.parties] or [None]
        for party in parties:
            if party is not None:
                _put(sheet, f"L{row}", ROLES[party.role])
                if party.is_writer:
                    _put(sheet, f"M{row}", party.first_name)
                    _put(sheet, f"N{row}", party.last_name)
                else:
                    _put(sheet, f"O{row}", party.name)
                _put(sheet, f"P{row}", party.ipi)
                _put(sheet, f"Q{row}", party.society)
                sheet[f"R{row}"].value = float(party.share) / 100
            row += 1
    if row - 1 > LAST_TEMPLATE_ROW:
        sheet.tables["CueDetails2"].ref = f"A{FIRST_ROW}:R{row - 1}"
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def filename(cue_sheet: CueSheet, today: datetime.date) -> str:
    """e.g. 'BH101 The Voyage cue sheet 2026-10-31.xlsx'."""
    header = cue_sheet.header
    stem = " ".join(p for p in (header.production_number, header.title) if p)
    safe = re.sub(r"[^\w .-]+", "", stem).strip() or "cue sheet"
    return f"{safe} cue sheet {today:%Y-%m-%d}.xlsx"
