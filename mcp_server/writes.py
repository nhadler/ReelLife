"""MCP tools that change cues: edits, new cues, versions, undo.

Each goes through the service the web app uses (the grid's edit service,
the new cue and new version dialogs' forms and authoring), so the same
validation applies, and each change is one change set that Undo, here or
in the app, reverts. Editors and admins only.

Values are taken the way a person would write them: a status or reel by
name, a choice by its code or label, timecodes as text, themes as names
(optionally "Sea 1:15" for a part of the cue). They are turned into what
the grid sends before the edit is made.
"""

import re
from typing import Any

from django.core.exceptions import ValidationError
from mcp.server.mcpserver.exceptions import ToolError

from accounts.models import Role
from cues import columns
from cues import forms
from cues import models
from cues import timecode
from cues.api import edits
from cues.api import grid
from cues.services import authoring
from cues.services import history
from cues.services import projects as project_service
from mcp_server import common

_KIND = {
    columns.Editor.TEXT: "text",
    columns.Editor.MARKDOWN: "text",
    columns.Editor.TIMECODE: "timecode, e.g. 01:00:10:00",
    columns.Editor.DURATION: "duration, m:ss or 00:00:30:00",
    columns.Editor.SELECT: "one of the options",
    columns.Editor.STATUS: "a status name",
    columns.Editor.TAGS: "list of names; 'Sea 1:15' plays for 1:15",
    columns.Editor.CHECKBOX: "true or false",
    columns.Editor.NUMBER: "number",
}
# '1:15' or '75' after a chip name: how long it plays, in seconds.
_CHIP_LENGTH = re.compile(r"^(?P<name>.*?)\s+(?:(?P<m>\d+):)?(?P<s>\d{1,2})$")


def _options(project: models.Project, field: str) -> list[str]:
    """The values a select-like field takes in this project."""
    if field == "status":
        return [s.label for s in project.statuses.all()]
    if field == "sub_status":
        return [s.label for s in project.sub_statuses.all()]
    if field in grid.LISTS:
        return [o.name for o in getattr(project, grid.LISTS[field]).all()]
    if field in models.DEPARTMENT_STATUS_FIELDS:
        return list(
            project.department_statuses.filter(field=field).values_list(
                "name", flat=True
            )
        )
    if field in grid.CHOICES:
        return [code for code, _ in grid.CHOICES[field] if code]
    return []


def cue_fields(project_id: int) -> list[dict[str, Any]]:
    """The cue fields `edit_cue` can change, with the values each takes."""
    common.user(Role.VIEWER)
    project = common.project(project_id)
    fields = [
        {
            "field": column.id,
            "label": column.label,
            "value": _KIND.get(column.editor, "text"),
            "options": _options(project, column.id),
        }
        for column in columns.COLUMNS
        if column.id in grid.EDITABLE
    ]
    fields.append(
        {
            "field": "picture",
            "label": "Reel",
            "value": "a reel name",
            "options": [p.name for p in project.pictures.order_by("order")],
        }
    )
    return fields


def _chip(entry: Any) -> dict[str, Any]:
    if isinstance(entry, dict):
        return entry
    text = str(entry).strip()
    match = _CHIP_LENGTH.match(text)
    if match and match["name"]:
        seconds = int(match["m"] or 0) * 60 + int(match["s"])
        return {"name": match["name"], "seconds": seconds}
    return {"name": text}


def _grid_value(cue: models.Cue, field: str, value: Any) -> Any:
    """`value` as the grid would send it for `field`."""
    project = cue.project
    if field == "status" and isinstance(value, str) and value:
        found = project.statuses.filter(label__iexact=value.strip()).first()
        if found is None:
            raise ToolError(
                f"No status {value!r}; this project has: "
                + ", ".join(_options(project, "status"))
            )
        return found.pk
    if field == "picture" and isinstance(value, str):
        reel = project.pictures.filter(name__iexact=value.strip()).first()
        if reel is None:
            raise ToolError(f"No reel {value!r} in this project.")
        return reel.pk
    if field in grid.CHOICES and isinstance(value, str):
        for code, label in grid.CHOICES[field]:
            if value.strip().lower() in {code.lower(), label.lower()}:
                return code
    if field in grid.LISTS:
        entries = value if isinstance(value, list) else [value]
        return [_chip(entry) for entry in entries if entry not in ("", None)]
    if field == "lane" and isinstance(value, str) and value.isdigit():
        return int(value)
    return value


def edit_cue(cue_id: int, field: str, value: Any) -> dict[str, Any]:
    """Changes one field of a cue (see cue_fields), as one undoable change."""
    user = common.user(Role.EDITOR)
    cue = common.cue(cue_id)
    try:
        edits.apply(cue, field, _grid_value(cue, field, value), user)
    except edits.EditError as exc:
        raise ToolError(str(exc)) from exc
    cue = common.cue(cue_id)
    return {"changed": field, "cue": common.cue_row(cue, cue.project.rate)}


def _reel_for(project: models.Project, reel: str, tc_in: str) -> Any:
    """The reel named, else the one whose current picture holds IN."""
    pictures = project.pictures.exclude(
        name=project_service.PLACEHOLDER_PICTURE
    ).order_by("order", "name")
    if reel:
        found = pictures.filter(name__iexact=reel.strip()).first()
        if found is None:
            raise ToolError(f"No reel {reel!r} in this project.")
        return found
    frames = None
    if tc_in:
        try:
            frames = timecode.parse(tc_in, project.rate)
        except timecode.TimecodeError as exc:
            raise ToolError(f"IN: {exc}") from exc
    for picture in pictures:
        current = picture.current_version()
        if (
            frames is not None
            and current
            and current.tc_in is not None
            and current.tc_out is not None
            and current.tc_in <= frames <= current.tc_out
        ):
            return picture
    if pictures.count() == 1:
        return pictures.first()
    raise ToolError("Say which reel the cue goes in.")


def new_cue(  # pylint: disable=too-many-arguments
    project_id: int,
    number: str,
    title: str = "",
    reel: str = "",
    tc_in: str = "",
    tc_out: str = "",
    status: str = "",
    cue_type: str = "SCORE",
    spotting_notes: str = "",
) -> dict[str, Any]:
    """Adds a cue with its v1, as the New Cue dialog does."""
    user = common.user(Role.EDITOR)
    project = common.project(project_id)
    picture = _reel_for(project, reel, tc_in)
    chosen = project_service.default_status(project)
    if status:
        chosen = project.statuses.filter(label__iexact=status.strip()).first()
        if chosen is None:
            raise ToolError(f"No status {status!r} in this project.")
    data = common.as_form_data(
        {
            "number": number,
            "title": title,
            "picture": picture.pk,
            "tc_in": tc_in,
            "tc_out": tc_out,
            "status": chosen.pk if chosen else "",
            "cue_type": cue_type,
            "usage": "BI",
            "spotting_notes": spotting_notes,
        }
    )
    form = forms.NewCueForm(data, project=project)
    if not form.is_valid():
        raise ToolError(common.form_errors(form))
    cue = form.save(commit=False)
    try:
        authoring.create_cue(
            cue,
            sub_status=form.cleaned_data["sub_status"],
            notes={
                name: form.cleaned_data[name]
                for name in models.VERSION_NOTE_FIELDS
            },
            user=user,
        )
    except ValidationError as exc:
        raise ToolError(" ".join(exc.messages)) from exc
    return {"created": common.cue_row(common.cue(cue.pk), project.rate)}


def add_version(  # pylint: disable=too-many-arguments
    cue_id: int,
    label: str = "",
    revision_notes: str = "",
    change_notes: str = "",
    status: str = "",
    tc_in: str = "",
    tc_out: str = "",
) -> dict[str, Any]:
    """Adds the next version of a cue, written to its reel's current picture.

    It starts from the current version's notes and the cue's timecodes, as
    the New Version dialog does; an Approved cue goes back to Composed
    (review reopens) unless `status` (e.g. Revisions) names another.
    """
    user = common.user(Role.EDITOR)
    cue = common.cue(cue_id)
    rate = cue.project.rate
    current = cue.current_version
    chosen = authoring.reopened_status(cue)
    if status:
        chosen = cue.project.statuses.filter(label__iexact=status).first()
        if chosen is None:
            raise ToolError(f"No status {status!r} in this project.")
    written_to = cue.picture.current_version()
    values: dict[str, Any] = {
        "label": label or authoring.next_version_label(cue),
        "written_to": written_to.pk if written_to else "",
        "tc_in": tc_in or common.tc(cue.tc_in, rate),
        "tc_out": tc_out or common.tc(cue.tc_out, rate),
        "seq_start": common.tc(cue.seq_start, rate),
        "status": chosen.pk if chosen else "",
        "update_cue": True,
        "revision_notes": revision_notes,
        "change_notes": change_notes,
        "review_status": models.CueVersion.ReviewStatus.choices[0][0],
    }
    if current is not None:
        for name in models.VERSION_NOTE_FIELDS:
            values[name] = getattr(current, name)
        values["sub_status"] = current.sub_status_id or ""
    form = forms.NewVersionForm(common.as_form_data(values), cue=cue)
    if not form.is_valid():
        raise ToolError(common.form_errors(form))
    version = form.save(commit=False)
    try:
        authoring.create_version(
            version,
            update_cue=form.cleaned_data["update_cue"],
            status=form.cleaned_data["status"],
            user=user,
        )
    except ValidationError as exc:
        raise ToolError(" ".join(exc.messages)) from exc
    cue = common.cue(cue_id)
    return {"added": version.label, "cue": common.cue_row(cue, rate)}


def undo_last_change(project_id: int) -> dict[str, str]:
    """Undoes your latest change in the project, as Ctrl+Z does in the app."""
    user = common.user(Role.EDITOR)
    project = common.project(project_id)
    latest = (
        project.change_sets.filter(
            user=user, undone_at__isnull=True, undo_of__isnull=True
        )
        .order_by("-created_at", "-id")
        .first()
    )
    if latest is None:
        raise ToolError("Nothing to undo.")
    try:
        history.undo(latest, user)
    except history.UndoError as exc:
        raise ToolError(str(exc)) from exc
    return {"undone": latest.label}


TOOLS = (edit_cue, new_cue, add_version, undo_last_change)
READ_TOOLS = (cue_fields,)
