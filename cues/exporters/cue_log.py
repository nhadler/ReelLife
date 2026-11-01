"""The cue log export, CSV and Excel.

Built from the grid's own rows, so each column reads exactly as it does
on screen. Cells that a spreadsheet could take for a formula are made
inert: CSV cells get a leading apostrophe, and Excel cells are written
as plain strings, which Excel never evaluates.
"""

from collections.abc import Iterable, Sequence
import csv
import dataclasses
import datetime
import io
import re
from typing import Any

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font

from cues import columns
from cues import models
from cues import timecode
from cues.api import grid
from cues.exporters import pdf
from cues.services import long_play
from cues.services import progress

# Characters that start a formula in Excel, LibreOffice and Sheets.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_GROUPS = {
    str(models.StatusKind.SONG): "Songs",
    str(models.StatusKind.LICENSE): "Licensed",
}
# Plain numbers and timecodes (e.g. -00:00:02:12) are safe as they are.
_INERT = re.compile(r"^[+-]?[0-9][0-9:;.]*$")


def csv_safe(text: str) -> str:
    """Neutralises text a spreadsheet would run as a formula."""
    if text.startswith(_FORMULA_START) and not _INERT.match(text):
        return "'" + text
    return text


@dataclasses.dataclass(frozen=True)
class Reel:
    """One picture's rows, with its subtotal."""

    name: str
    rows: list[list[str]]
    cues: int
    frames: int


@dataclasses.dataclass(frozen=True)
class CueLog:
    """Everything an export needs, as display text."""

    project: models.Project
    headers: list[str]
    reels: list[Reel]
    totals: list[list[str]]
    progress: list[list[str]]
    # Lines under the title, e.g. how Long Play Time was worked out.
    notes: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class _Labels:
    """Names for the ids grid rows carry."""

    statuses: dict[int, str]
    turnovers: dict[int, str]
    sub_statuses: dict[int, str]


def _display(column: str, value: Any, labels: _Labels) -> str:
    """A grid row value as the grid shows it."""
    if value is None or value == "":
        return ""
    if isinstance(value, dict):
        return str(value.get("display", ""))
    if isinstance(value, bool):
        return "Yes" if value else ""
    if column == "status":
        return labels.statuses.get(value, "")
    if column == "written_to":
        return labels.turnovers.get(value, "")
    if column == "sub_status":
        return labels.sub_statuses.get(value, "")
    if column in grid.CHOICES:
        return dict(grid.CHOICES[column]).get(value, str(value))
    return str(value)


def _music_totals(
    project: models.Project, rate: timecode.Rate
) -> list[list[str]]:
    """Compositions, songs, licensed and total: count, duration, share."""
    sums = {"Compositions": [0, 0], "Songs": [0, 0], "Licensed": [0, 0]}
    rows = project.cues.filter(active=True).values_list(
        "tc_in", "tc_out", "status__kind"
    )
    for tc_in, tc_out, kind in rows:
        info = models.STATUS_KIND_INFO.get(kind or models.StatusKind.TO_DO)
        if info is None or not info[2]:
            continue
        frames = (
            max(tc_out - tc_in, 0)
            if tc_in is not None and tc_out is not None
            else 0
        )
        group = _GROUPS.get(str(kind), "Compositions")
        sums[group][0] += 1
        sums[group][1] += frames
    total_cues = sum(c for c, _ in sums.values())
    total_frames = sum(f for _, f in sums.values())
    lines = [["Music totals", "Cues", "Duration", "Share"]]
    for name, (cues, frames) in sums.items():
        share = round(100 * frames / total_frames) if total_frames else 0
        lines.append(
            [
                name,
                str(cues),
                timecode.format_duration(frames, rate),
                f"{share}%",
            ]
        )
    lines.append(
        [
            "Total",
            str(total_cues),
            timecode.format_duration(total_frames, rate),
            "",
        ]
    )
    lines.append(
        [
            "Runtime",
            "",
            timecode.format_duration(progress.runtime(project), rate),
            "",
        ]
    )
    return lines


def _progress(project: models.Project) -> list[list[str]]:
    composed, remaining = progress.for_project(project).summary(project.rate)
    return [["Progress"], [composed], [remaining]]


def _by_reel(rows: list[dict[str, Any]], table: list[list[str]]) -> list[Reel]:
    """Groups consecutive rows by reel, totalling each group."""
    reels: list[Reel] = []
    for row, line in zip(rows, table, strict=True):
        name = row["_picture"]["name"]
        duration = row.get("duration")
        frames = max(duration["frames"], 0) if duration else 0
        if not reels or reels[-1].name != name:
            reels.append(Reel(name, [], 0, 0))
        last = reels[-1]
        reels[-1] = Reel(
            name, [*last.rows, line], last.cues + 1, last.frames + frames
        )
    return reels


# Columns that are positions in a reel, which Long Play Time moves onto
# the joined timeline; durations and offsets stay as they are.
POSITIONS = (
    "seq_start",
    "tc_in",
    "tc_out",
    "version_seq_start",
    "version_in",
    "version_out",
)


def _long_play(
    project: models.Project,
    rows: list[dict[str, Any]],
    ctx: grid.Context,
    options: long_play.Options,
) -> tuple[list[dict[str, Any]], list[str]]:
    """The rows with positions on one timeline, and lines saying how."""
    rate = project.rate
    first = long_play.start(options, rate)
    shifts = long_play.shifts(long_play.reels(project), first)
    moved = []
    unplaced: dict[int, str] = {}
    for row in rows:
        picture = row["_picture"]
        shift = shifts.get(picture["id"])
        if shift is None:
            unplaced[picture["id"]] = project.picture_title(picture["name"])
        moved.append(
            row
            | {
                column: ctx.tc(
                    long_play.place(
                        row[column]["frames"] if row[column] else None,
                        shift,
                        rate,
                    )
                )
                for column in POSITIONS
            }
        )
    leader = timecode.format_timecode(options.leader, rate, leading_zero=True)
    notes = [
        "Long Play Time: "
        f"{project.reels_word.lower()} end to end from "
        f"{timecode.format_timecode(first, rate, leading_zero=True)}"
        + (f", after a {leader} leader." if options.leader else ".")
    ]
    if unplaced:
        notes.append(
            "Positions are blank for cues on "
            + ", ".join(unplaced.values())
            + f": no {project.reel_word.lower()} IN and OUT to place them by."
        )
    return moved, notes


def build(  # pylint: disable=too-many-arguments
    project: models.Project,
    column_ids: Sequence[str],
    *,
    cue_ids: Iterable[int] | None = None,
    exclude_empty: bool = False,
    totals: bool = False,
    show_progress: bool = False,
    long_play_time: long_play.Options | None = None,
) -> CueLog:
    """Builds the cue log's text.

    Args:
        project: The project.
        column_ids: Columns in order (ids from cues/columns.py; unknown
            ones are skipped).
        cue_ids: Only these cues, in this order (the grid's filtered and
            sorted rows); None means every cue, by reel then IN.
        exclude_empty: Leave out columns with no value in any row.
        totals: Add the music totals block.
        show_progress: Add the progress block.
        long_play_time: Put positions on the reels joined end to end
            (services/long_play.py), saying how in the log's notes.

    Returns:
        The cue log, grouped by reel.
    """
    ids = [c for c in column_ids if c in columns.BY_ID]
    ctx = grid.Context.build(project)
    labels = _Labels(
        statuses=dict(project.statuses.values_list("id", "label")),
        turnovers=ctx.version_labels,
        sub_statuses=dict(project.sub_statuses.values_list("id", "label")),
    )
    cues = list(grid.cues_queryset(project))
    if cue_ids is not None:
        by_id = {cue.pk: cue for cue in cues}
        cues = [by_id[pk] for pk in cue_ids if pk in by_id]
    rows = [grid.row(cue, ctx) for cue in cues]
    notes: list[str] = []
    if long_play_time is not None:
        rows, notes = _long_play(project, rows, ctx, long_play_time)
    table = [[_display(c, row.get(c), labels) for c in ids] for row in rows]
    if exclude_empty:
        keep = [i for i in range(len(ids)) if any(line[i] for line in table)]
        ids = [ids[i] for i in keep]
        table = [[line[i] for i in keep] for line in table]
    return CueLog(
        project=project,
        headers=[columns.BY_ID[c].label for c in ids],
        reels=_by_reel(rows, table),
        totals=_music_totals(project, project.rate) if totals else [],
        progress=_progress(project) if show_progress else [],
        notes=notes,
    )


def to_csv(log: CueLog) -> bytes:
    """One header row, then one row per cue with its reel first."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Reel", *log.headers])
    for reel in log.reels:
        for line in reel.rows:
            writer.writerow([csv_safe(v) for v in [reel.name, *line]])
    # A byte order mark so Excel reads the file as UTF-8.
    return ("﻿" + out.getvalue()).encode("utf-8")


def to_xlsx(log: CueLog, today: datetime.date) -> bytes:
    """A titled sheet, optional totals and progress, reels with subtotals."""
    book = Workbook(write_only=True)
    sheet = book.create_sheet("Cue Log")
    bold = Font(bold=True)

    def cells(values: Sequence[str], *, strong: bool = False) -> list[Any]:
        row = []
        for value in values:
            cell = WriteOnlyCell(sheet, value=value)
            cell.data_type = "s"  # always text: never a formula
            if strong:
                cell.font = bold
            row.append(cell)
        return row

    project = log.project
    title = " ".join(p for p in (project.production_number, project.title) if p)
    sheet.append(cells([f"{title}: cue log"], strong=True))
    sheet.append(cells([f"Exported {today:%-d %b %Y}, {project.rate.label}"]))
    for note in log.notes:
        sheet.append(cells([note]))
    for block in (log.totals, log.progress):
        if block:
            sheet.append([])
            for index, line in enumerate(block):
                sheet.append(cells(line, strong=index == 0))
    sheet.append([])
    sheet.append(cells(log.headers, strong=True))
    rate = project.rate
    for reel in log.reels:
        sheet.append(cells([f"Reel {reel.name}"], strong=True))
        for line in reel.rows:
            sheet.append(cells(line))
        sheet.append(
            cells(
                [
                    f"Reel {reel.name}: {reel.cues} "
                    f"{'cue' if reel.cues == 1 else 'cues'}, "
                    f"{timecode.format_duration(reel.frames, rate)}"
                ]
            )
        )
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def to_pdf(log: CueLog, today: datetime.date) -> bytes:
    """Landscape Letter: blocks first, then each reel with its subtotal."""
    rate = log.project.rate
    reels = [
        {
            "name": reel.name,
            "rows": reel.rows,
            "cues": reel.cues,
            "duration": timecode.format_duration(reel.frames, rate),
        }
        for reel in log.reels
    ]
    return pdf.render(
        "cues/pdf/cue_log.html",
        {
            "project": log.project,
            "log": {"headers": log.headers, "reels": reels},
            "notes": log.notes,
            "blocks": [block for block in (log.totals, log.progress) if block],
            "today": today,
        },
    )


def filename(
    project: models.Project,
    today: datetime.date,
    suffix: str,
    report: str = "Cue Log",
) -> str:
    """e.g. 'DEMO101 The Lighthouse Keeper Cue Log 2026-10-30.xlsx'."""
    parts = [
        project.production_number,
        project.title,
        report,
        f"{today:%Y-%m-%d}",
    ]
    name = " ".join(p for p in parts if p)
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "-", name) + suffix
