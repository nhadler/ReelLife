"""The Tools menu and the conform assistant over MCP.

Tools that change many cues at once (new cues, add versions, split,
merge, renumber, shift, move, copy, delete; shifting reels after a
turnover) answer first with a preview and a `confirm` code; called
again with that code, they make the change, as one undoable change set.
The tools are the dialogs' own forms (cues/tool_forms.py), so their
rules and wording are the app's.
"""

from typing import Any, cast

from django import forms as django_forms
from django.core.exceptions import ValidationError
from mcp.server.mcpserver.exceptions import ToolError

from accounts.models import Role
from cues import models
from cues import tool_forms
from cues.services import conform
from cues.services import tools
from mcp_server import common

_NEXT = "Show the user this preview; if they agree, call again with confirm."


def _choices(field: django_forms.Field) -> list[dict[str, str]]:
    if isinstance(field, django_forms.ModelChoiceField):
        return [
            {"value": str(obj.pk), "label": field.label_from_instance(obj)}
            for obj in (field.queryset if field.queryset is not None else [])
        ]
    if isinstance(field, django_forms.ChoiceField):
        choices: Any = field.choices
        return [
            {"value": str(value), "label": str(label)}
            for value, label in choices
            if value != ""
        ]
    return []


def _form(
    project_id: int,
    tool: str,
    cue_ids: list[int],
    options: dict[str, Any] | None,
) -> tool_forms.ToolForm:
    form_class = tool_forms.FORMS.get(tool)
    if form_class is None:
        raise ToolError(
            f"No tool {tool!r}; choose one of: {', '.join(tool_forms.FORMS)}."
        )
    project = common.project(project_id)
    cues = common.cues(project, cue_ids)
    form = form_class(None, project=project, cues=cues)
    data = common.as_form_data(options or {})
    # Choices may be given by their label, e.g. a reel's name.
    for name, field in form.fields.items():
        value = data.get(name)
        if value and isinstance(field, django_forms.ChoiceField):
            for choice in _choices(field):
                if value.strip().lower() == choice["label"].lower():
                    data[name] = choice["value"]
    return form_class(data, project=project, cues=cues)


def describe_cue_tool(
    project_id: int, tool: str, cue_ids: list[int]
) -> dict[str, Any]:
    """A tool's options for these cues: names, labels, choices, defaults."""
    common.user(Role.VIEWER)
    form = _form(project_id, tool, cue_ids, None)
    return {
        "tool": form.heading(),
        "cues_problem": form.selection_error(),
        "options": [
            {
                "name": name,
                "label": str(field.label or name),
                "required": field.required,
                "help": str(field.help_text or ""),
                "choices": _choices(field),
            }
            for name, field in form.fields.items()
        ],
    }


def cue_tool(
    project_id: int,
    tool: str,
    cue_ids: list[int],
    options: dict[str, Any] | None = None,
    confirm: str = "",
) -> dict[str, Any]:
    """Runs a Tools menu tool: a preview first, then the change on confirm.

    Tools: new, versions, split, merge, renumber, shift, move, copy,
    delete. See describe_cue_tool for each one's options.
    """
    user = common.user(Role.EDITOR)
    form = _form(project_id, tool, cue_ids, options)
    if not form.is_valid():
        raise ToolError(common.form_errors(form))
    preview = form.preview()
    shown = {
        "columns": list(preview.columns),
        "rows": [list(row) for row in preview.rows],
        "summary": preview.summary,
    }
    code = common.fingerprint(
        tool, project_id, cue_ids, sorted(cast(Any, form.data).lists()), shown
    )
    if confirm != code:
        return {
            "tool": form.heading(),
            "preview": shown,
            "confirm": code,
            "next": _NEXT,
        }
    try:
        form.apply(user)
    except tools.ToolError as exc:
        raise ToolError(str(exc)) from exc
    except ValidationError as exc:
        raise ToolError(" ".join(exc.messages)) from exc
    return {"done": form.heading(), "summary": preview.summary}


def _turnover(
    project: models.Project, turnover_id: int | None
) -> models.Turnover | None:
    turnovers = project.turnovers.order_by("-received_date", "-pk")
    if turnover_id is None:
        return turnovers.first()
    found = turnovers.filter(pk=turnover_id).first()
    if found is None:
        raise ToolError(f"No turnover {turnover_id} in this project.")
    return found


def conform_status(
    project_id: int, turnover_id: int | None = None
) -> dict[str, Any]:
    """Reels whose IN moved in a turnover, and cues on older picture.

    The turnover is the latest unless `turnover_id` names one.
    """
    common.user(Role.VIEWER)
    project = common.project(project_id)
    turnover = _turnover(project, turnover_id)
    moves = conform.moved_reels(turnover) if turnover else []
    return {
        "turnover": str(turnover) if turnover else "",
        "moved_reels": [
            {
                "reel": move.picture.name,
                "moved_by": move.label(project.rate),
                "cues": move.cues,
            }
            for move in moves
        ],
        "on_older_picture": [
            {
                "cue_id": stale.cue.pk,
                "number": stale.cue.number,
                "written_to": stale.written_to,
                "start_offset": stale.start_offset,
                "duration_difference": stale.duration_difference,
            }
            for stale in conform.needing_conform(project)
        ],
    }


def conform_shift(
    project_id: int,
    reels: list[str],
    turnover_id: int | None = None,
    confirm: str = "",
) -> dict[str, Any]:
    """Shifts the named reels' cues by however far each reel's IN moved."""
    user = common.user(Role.EDITOR)
    project = common.project(project_id)
    turnover = _turnover(project, turnover_id)
    wanted = {reel.strip().lower() for reel in reels}
    moves = [
        move
        for move in (conform.moved_reels(turnover) if turnover else [])
        if move.picture.name.lower() in wanted and move.cues
    ]
    if not moves:
        raise ToolError(
            "None of those reels has cues left to shift (none moved, or "
            "they were shifted already); see conform_status."
        )
    shown = [
        {"reel": m.picture.name, "by": m.label(project.rate), "cues": m.cues}
        for m in moves
    ]
    code = common.fingerprint("conform_shift", project_id, shown)
    if confirm != code:
        return {"preview": shown, "confirm": code, "next": _NEXT}
    try:
        moved = conform.shift_reels(project, moves, user)
    except tools.ToolError as exc:
        raise ToolError(str(exc)) from exc
    return {"shifted_cues": moved}


def mark_conformed(
    project_id: int, cue_ids: list[int], new_version: bool = False
) -> dict[str, Any]:
    """Brings cues on older picture onto their reels' current picture.

    Moves each one's current version there, or adds a new version there.
    """
    user = common.user(Role.EDITOR)
    project = common.project(project_id)
    wanted = set(cue_ids)
    stale = [
        s.cue for s in conform.needing_conform(project) if s.cue.pk in wanted
    ]
    if not stale:
        raise ToolError("None of those cues are on older picture.")
    conform.mark_conformed(project, stale, new_version=new_version, user=user)
    return {
        "conformed": [cue.number for cue in stale],
        "how": "new versions" if new_version else "in place",
    }


TOOLS = (cue_tool, conform_shift, mark_conformed)
READ_TOOLS = (describe_cue_tool, conform_status)
