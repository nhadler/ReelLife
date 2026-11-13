"""Importing and exporting over MCP.

Imports read a file sent as base64, through the import dialog's own form
and planner: the first call answers with the preview (new, updated and
skipped cues, warnings, cues missing from the file) and a `confirm`
code; called again with it, the import is applied as one undoable change
set. Exports come back as files: cue logs (Excel, CSV, PDF), the wall
chart, version notes and the PRO cue sheet with its checklist.
"""

import base64
import binascii
from typing import Any

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import BlobResourceContents
from mcp_types import EmbeddedResource
from mcp_types import TextContent

from accounts.models import Role
from cues import models
from cues import transfer_forms
from cues.api import grid
from cues.exporters import cue_log
from cues.exporters import version_notes
from cues.exporters import wall_chart
from cues.exporters.cue_sheet import generic
from cues.exporters.cue_sheet import model as cue_sheet
from cues.exporters.cue_sheet import rapidcue
from cues.importers import apply
from cues.importers import base
from cues.importers import merge
from cues.services import imports
from cues.services import long_play
from mcp_server import common

_TYPES = {
    ".csv": "text/csv",
    ".xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
    ".pdf": "application/pdf",
}


def _file(name: str, body: bytes, note: str) -> list[Any]:
    """A sentence and the file itself, for the client to save or show."""
    suffix = name[name.rfind(".") :]
    return [
        TextContent(text=note),
        EmbeddedResource(
            resource=BlobResourceContents(
                uri=f"reeltime://exports/{name.replace(' ', '%20')}",
                mime_type=_TYPES[suffix],
                blob=base64.b64encode(body).decode(),
            )
        ),
    ]


# A file of imports.MAX_BYTES in base64. reeltime/asgi.py sizes the MCP
# server's request body limit from this, so such a file always fits.
MAX_BASE64 = imports.MAX_BYTES * 4 // 3 + 4


def _decode(content_base64: str) -> bytes:
    if len(content_base64) > MAX_BASE64:
        raise ToolError("Send a file under 10 MB.")
    try:
        return base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ToolError("content_base64 isn't valid base64.") from exc


def import_cues(  # pylint: disable=too-many-arguments,too-many-locals
    project_id: int,
    filename: str,
    content_base64: str,
    kind: str = "detect",
    options: dict[str, Any] | None = None,
    missing: str = "leave",
    confirm: str = "",
) -> dict[str, Any]:
    """Imports cues from a Pro Tools, Cubase, MIDI or spreadsheet file.

    `kind`: detect, protools, cubase, midi or spreadsheet. `options` are
    the import dialog's: method (clips or markers), prefixes, start,
    mapping, start_number, picture, update_titles. `missing`: leave,
    inactive or omit, for cues the file doesn't mention. New cues whose
    numbers don't fit the project's Valid Cue Numbers are left out.
    """
    user = common.user(Role.EDITOR)
    project = common.project(project_id)
    data = common.as_form_data(
        {
            "kind": kind,
            "missing": missing,
            **(options or {}),
        }
    )
    upload = SimpleUploadedFile(filename, _decode(content_base64))
    form = transfer_forms.ImportForm(data, {"file": upload}, project=project)
    if not form.is_valid():
        raise ToolError(common.form_errors(form))
    body, found_kind = form.data_and_kind()
    source = form.source(found_kind)
    try:
        parsed = imports.parse(project, body, source)
        preview = imports.plan(project, parsed, form.options(found_kind))
    except (base.ImportFormatError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    code = imports.fingerprint(preview)[:16]
    rate = project.rate
    # A new cue's reel, or the reel an updated cue moves to.
    reels = dict(project.pictures.values_list("id", "name"))
    if confirm != code:
        return {
            "read_as": preview.source,
            "new": preview.count(merge.Action.NEW),
            "updated": preview.count(merge.Action.UPDATE),
            "skipped": preview.count(merge.Action.SKIP),
            "changes": [
                {
                    "action": change.action,
                    "number": change.row.number,
                    "title": change.new_title or change.row.title,
                    "in": common.tc(change.row.tc_in, rate),
                    "out": common.tc(change.row.tc_out, rate),
                    "old_in": common.tc(change.old_in, rate),
                    "old_out": common.tc(change.old_out, rate),
                    "reel": reels.get(change.picture_id or -1, ""),
                    "reason": change.reason,
                }
                for change in preview.changes
            ],
            "warnings": preview.warnings,
            "not_in_file": [m.number for m in preview.missing],
            "confirm": code,
            "next": "Show the user this preview; if they agree, call again "
            "with confirm.",
        }
    try:
        result = apply.apply(
            project,
            preview,
            user=user,
            filename=filename,
            missing_action=apply.MissingAction(
                form.cleaned_data.get("missing") or apply.MissingAction.LEAVE
            ),
        )
    except ValidationError as exc:
        raise ToolError(" ".join(exc.messages)) from exc
    if found_kind in {imports.Kind.CUBASE, imports.Kind.MIDI}:
        project.daw_start = source.start_frames
        project.save(update_fields=["daw_start", "updated_at"])
    return {
        "imported": filename,
        "new": result.created,
        "updated": result.updated,
        "not_in_file_changed": result.missing_changed,
    }


def _columns(project: models.Project, view: str) -> list[str]:
    views = grid.views_for(project)
    if not view:
        return [entry["id"] for entry in views[0]["columns"]]
    for found in views:
        if found["name"].lower() == view.strip().lower():
            return [entry["id"] for entry in found["columns"]]
    names = ", ".join(found["name"] for found in views)
    raise ToolError(f"No view {view!r}; this project has: {names}.")


def export(  # pylint: disable=too-many-arguments,too-many-locals
    project_id: int,
    report: str = "cue_log",
    file_format: str = "xlsx",
    view: str = "",
    totals: bool = True,
    all_versions: bool = False,
    long_play_time: bool = False,
    start_hour: int = 1,
    leader: str = "",
) -> list[Any]:
    """A project's cue log, wall chart or version notes, as a file.

    The cue log is xlsx, csv or pdf, with columns from a view; the wall
    chart and version notes are pdf. `long_play_time` (cue log, not for
    long-play projects) joins the reels into one timeline starting at
    `start_hour` (0 or 1) after a `leader` (e.g. 0:00:08:00); cues with
    no reel IN and OUT to place them by get blank positions.
    """
    common.user(Role.VIEWER)
    project = common.project(project_id)
    today = timezone.localdate()
    if report == "wall_chart":
        name = cue_log.filename(project, today, ".pdf", "Wall Chart")
        body = wall_chart.wall_chart(project, today=today)
        return _file(name, body, f"The wall chart: {name}")
    if report == "version_notes":
        name = cue_log.filename(project, today, ".pdf", "Version Notes")
        body = version_notes.to_pdf(
            project, all_versions=all_versions, today=today
        )
        return _file(name, body, f"The version notes: {name}")
    if report != "cue_log":
        raise ToolError("report is cue_log, wall_chart or version_notes.")
    if file_format not in {"xlsx", "csv", "pdf"}:
        raise ToolError("file_format is xlsx, csv or pdf.")
    options = None
    if long_play_time:
        if project.long_play:
            raise ToolError("A long-play project is one timeline already.")
        try:
            options = long_play.options(start_hour, leader, project.rate)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
    log = cue_log.build(
        project,
        _columns(project, view),
        totals=totals,
        show_progress=totals,
        long_play_time=options,
    )
    body = {
        "csv": lambda: cue_log.to_csv(log),
        "pdf": lambda: cue_log.to_pdf(log, today),
        "xlsx": lambda: cue_log.to_xlsx(log, today),
    }[file_format]()
    name = cue_log.filename(project, today, f".{file_format}")
    return _file(name, body, " ".join([f"The cue log: {name}.", *log.notes]))


def _cue_sheet(
    project_id: int, revision: bool = False, include_inactive: bool = False
) -> tuple[cue_sheet.CueSheet, list[str], str]:
    """The project's cue sheet, its checklist and its RapidCue file name."""
    common.user(Role.VIEWER)
    project = common.project(project_id)
    today = timezone.localdate()
    sheet = cue_sheet.build(
        project,
        cue_sheet.Options(revision=revision, include_inactive=include_inactive),
        today,
    )
    issues = [
        f"{issue.number}: {issue.message}" if issue.number else issue.message
        for issue in cue_sheet.checklist(sheet)
    ]
    return sheet, issues, rapidcue.filename(sheet, today)


def cue_sheet_export(
    project_id: int,
    file_format: str = "rapidcue",
    revision: bool = False,
    include_inactive: bool = False,
) -> list[Any]:
    """The PRO cue sheet (RapidCue Excel or CSV) and what a PRO would query."""
    sheet, issues, name = _cue_sheet(project_id, revision, include_inactive)
    if file_format == "csv":
        body, name = generic.to_csv(sheet), name.removesuffix(".xlsx") + ".csv"
    elif file_format == "rapidcue":
        body = rapidcue.to_xlsx(sheet)
    else:
        raise ToolError("file_format is rapidcue or csv.")
    note = (
        "Checklist: " + "; ".join(issues)
        if issues
        else "Checklist: nothing a PRO would query."
    )
    return _file(name, body, f"The cue sheet: {name}. {note}")


def cue_sheet_csv(project_id: int) -> str:
    """The cue sheet as CSV text, for the cue sheet resource."""
    sheet = _cue_sheet(project_id)[0]
    return generic.to_csv(sheet).decode("utf-8-sig")


TOOLS = (import_cues,)
READ_TOOLS = (export, cue_sheet_export)
