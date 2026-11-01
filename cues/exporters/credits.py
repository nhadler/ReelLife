"""The Credits list as CSV or Excel (writers and publishers, every project).

A row per party: kind, first and last name (writers), company name
(publishers), PRO, IPI/CAE number, whether it's the composer's own, and
how many cues credit it. Cells are text, never formulas: Excel cells are
written as strings and CSV cells that could start a formula get an
apostrophe (cue_log.csv_safe).
"""

from collections.abc import Sequence
import csv
import datetime
import io
from typing import Any

from django.db.models import Count
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font

from cues import models
from cues.exporters import cue_log

HEADERS = (
    "Kind",
    "First Name",
    "Last Name",
    "Company Name",
    "PRO",
    "IPI/CAE #",
    "Composer's Own",
    "Cues",
)


def rows() -> list[list[str]]:
    """Writers by last name, then publishers by name."""
    cues = dict(
        models.CueShare.objects.values("party")
        .annotate(count=Count("cue", distinct=True))
        .values_list("party", "count")
    )
    parties = sorted(
        models.Party.objects.all(),
        key=lambda p: (
            p.kind != models.Party.Kind.WRITER,
            (p.last_name or p.name).lower(),
            p.first_name.lower(),
        ),
    )
    found = []
    for party in parties:
        writer = party.kind == models.Party.Kind.WRITER
        found.append(
            [
                party.get_kind_display(),
                party.first_name if writer else "",
                (party.last_name or party.name) if writer else "",
                "" if writer else party.name,
                party.society,
                party.ipi,
                "Yes" if party.is_mine else "",
                str(cues.get(party.pk, 0)),
            ]
        )
    return found


def to_csv() -> bytes:
    """A header row, then one row per party."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(HEADERS)
    for row in rows():
        writer.writerow([cue_log.csv_safe(value) for value in row])
    # A byte order mark so Excel reads the file as UTF-8.
    return ("﻿" + out.getvalue()).encode("utf-8")


def to_xlsx() -> bytes:
    """One sheet with bold headings; every cell text."""
    book = Workbook(write_only=True)
    sheet = book.create_sheet("Credits")

    def cells(values: Sequence[str], *, strong: bool = False) -> list[Any]:
        row = []
        for value in values:
            cell = WriteOnlyCell(sheet, value=value)
            cell.data_type = "s"  # always text: never a formula
            if strong:
                cell.font = Font(bold=True)
            row.append(cell)
        return row

    sheet.append(cells(HEADERS, strong=True))
    for row in rows():
        sheet.append(cells(row))
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def filename(today: datetime.date, suffix: str) -> str:
    """e.g. 'ReelLife Credits 2026-10-31.csv'."""
    return f"ReelLife Credits {today:%Y-%m-%d}{suffix}"
