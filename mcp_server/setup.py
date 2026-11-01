"""Setting a project up over MCP.

Creating a project, logging a turnover (its reels' IN and OUT), setting
deadlines, adding writers and publishers to Credits, a project's default
shares, and its notes: the steps an editor takes in the app before and
around writing cues, through the same services the app's pages use.
"""

import datetime
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any, cast

from django.core.exceptions import ValidationError
from mcp.server.mcpserver.exceptions import ToolError

from accounts.models import Role
from cues import models
from cues import timecode
from cues.services import history
from cues.services import projects as project_service
from cues.services import shares
from cues.services import turnovers
from mcp_server import common


def _date(text: str, what: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(text.strip())
    except ValueError as exc:
        raise ToolError(f"{what}: use YYYY-MM-DD, e.g. 2026-11-03.") from exc


def _messages(exc: ValidationError) -> str:
    return " ".join(exc.messages)


def create_project(
    title: str,
    frame_rate: str = "23.976",
    drop_frame: bool = False,
    production_number: str = "",
    kind: str = "film",
) -> dict[str, Any]:
    """Creates a project with the default statuses, lists and views.

    `frame_rate`: 23.976, 24, 25, 29.97, 30, 50, 59.94 or 60. `kind`:
    film or episode. Reels come with its first turnover (log_turnover).
    """
    common.user(Role.EDITOR)
    try:
        project = project_service.create_project(
            title=title.strip(),
            frame_rate=frame_rate,
            drop_frame=drop_frame,
            production_number=production_number.strip(),
            kind=kind,
        )
    except ValidationError as exc:
        raise ToolError(_messages(exc)) from exc
    return common.project_brief(project)


def log_turnover(
    project_id: int,
    label: str,
    received_date: str,
    reels: list[dict[str, str]],
    notes: str = "",
) -> dict[str, Any]:
    """Logs a turnover: the picture each reel is now at.

    Each reel is e.g. {"name": "1", "in": "01:00:00:00", "out": "01:20:00:00"}.
    New reel names add reels; reels left out keep their picture.
    """
    user = common.user(Role.EDITOR)
    project = common.project(project_id)
    entries = []
    for reel in reels:
        name = str(reel.get("name", "")).strip()
        if not name:
            raise ToolError("Every reel needs a name, e.g. 1.")
        try:
            tc_in, tc_out = (
                timecode.parse(reel[key], project.rate)
                if reel.get(key)
                else None
                for key in ("in", "out")
            )
        except timecode.TimecodeError as exc:
            raise ToolError(f"{project.picture_title(name)}: {exc}") from exc
        entries.append(
            turnovers.ReelEntry(
                project.pictures.filter(name=name).first(), name, tc_in, tc_out
            )
        )
    try:
        turnover = turnovers.create_turnover(
            project,
            label=label.strip(),
            received_date=_date(received_date, "received_date"),
            notes=notes,
            reels=entries,
            user=user,
        )
    except ValidationError as exc:
        raise ToolError(_messages(exc)) from exc
    return {"turnover_id": turnover.pk, "label": str(turnover)}


def set_deadline(
    project_id: int,
    kind: str,
    end_date: str,
    start_date: str = "",
    label: str = "",
) -> dict[str, Any]:
    """Sets a deadline, replacing one of the same kind.

    `kind`: composition, orchestration, mix, delivery, or custom (give it
    a label; custom deadlines are added, not replaced).
    """
    common.user(Role.EDITOR)
    project = common.project(project_id)
    if kind not in models.Deadline.Kind.values:
        raise ToolError(
            "kind is one of: " + ", ".join(models.Deadline.Kind.values)
        )
    deadline = (
        project.deadlines.filter(kind=kind).first()
        if kind != models.Deadline.Kind.CUSTOM
        else None
    ) or models.Deadline(project=project, kind=kind)
    deadline.label = label
    deadline.end_date = _date(end_date, "end_date")
    deadline.start_date = (
        _date(start_date, "start_date") if start_date else None
    )
    try:
        deadline.full_clean()
    except ValidationError as exc:
        raise ToolError(_messages(exc)) from exc
    deadline.save()
    return {"deadline": str(deadline), "end_date": str(deadline.end_date)}


def add_credit(  # pylint: disable=too-many-arguments
    kind: str,
    first_name: str = "",
    last_name: str = "",
    company_name: str = "",
    society: str = "",
    ipi: str = "",
    composers_own: bool = False,
) -> dict[str, Any]:
    """Adds a writer or publisher to Credits, the list all projects share.

    A writer has a first and last name, a publisher a company name. One
    already in Credits is reused, not added twice.
    """
    user = common.user(Role.EDITOR)
    kinds = models.Party.Kind
    if kind not in kinds.values:
        raise ToolError("kind is writer or publisher.")
    party = models.Party(
        kind=kind,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        # A writer's full name comes from the two halves (Party.clean).
        name=f"{first_name.strip()} {last_name.strip()}".strip()
        if kind == kinds.WRITER
        else company_name.strip(),
        society=society.strip().upper(),
        ipi=ipi.strip(),
        is_mine=composers_own,
    )
    try:
        party.full_clean()
    except ValidationError as exc:
        raise ToolError(_messages(exc)) from exc
    existing = models.Party.objects.filter(kind=kind, name=party.name).first()
    if existing is not None:
        return {"party_id": existing.pk, "name": str(existing), "new": False}
    with history.recording(None, f"Add {party} to Credits", user):
        party.save()
    return {"party_id": party.pk, "name": str(party), "new": True}


def set_default_shares(
    project_id: int, defaults: list[dict[str, Any]]
) -> dict[str, Any]:
    """Sets the shares new compositions start with, replacing the old ones.

    e.g. [{"party_id": 1, "role": "C", "share": 100}]. Writers' shares add
    to 100%, and publishers' too. Roles: C, A, CA, AR, AD, E, SE, AM.
    """
    user = common.user(Role.EDITOR)
    project = common.project(project_id)
    lines = []
    for entry in defaults:
        party_id = entry.get("party_id")
        party = (
            models.Party.objects.filter(pk=party_id).first()
            if isinstance(party_id, int)
            else None
        )
        if party is None:
            raise ToolError(f"No credit {entry.get('party_id')}.")
        try:
            share = Decimal(str(entry.get("share", "")))
        except InvalidOperation as exc:
            raise ToolError("A share is a percentage, e.g. 50.") from exc
        lines.append(shares.Line(party, str(entry.get("role", "")), share))
    problems = shares.problems(lines)
    if problems:
        raise ToolError(" ".join(problems))
    with history.recording(project, "Set default shares", user):
        project.default_shares.all().delete()
        for order, line in enumerate(lines):
            models.DefaultShare.objects.create(
                project=project,
                party=cast(models.Party, line.party),
                role=line.role,
                share=line.share,
                order=order,
            )
    return {
        "defaults": [f"{x.party} {x.role} {x.share}%" for x in lines],
        "note": "New compositions get these; existing cues keep theirs.",
    }


def set_project_note(
    project_id: int, text: str, kind: str = "general", append: bool = True
) -> dict[str, Any]:
    """Writes a project note, adding to what's there unless told not to.

    `kind`: general, or the project-wide side of a cue note kind:
    spotting, notes, revision, conform, orchestration, recording, mix,
    cue_sheet.
    """
    user = common.user(Role.EDITOR)
    project = common.project(project_id)
    if kind not in models.NoteKind.values:
        raise ToolError("kind is one of: " + ", ".join(models.NoteKind.values))
    note = project.notes.filter(kind=kind).first() or models.ProjectNote(
        project=project, kind=kind
    )
    new = f"{note.text.rstrip()}\n\n{text}".strip() if append else text
    label = f"Edit project notes: {models.NoteKind(kind).label}"
    with history.recording(project, label, user):
        note.text = new
        note.full_clean()
        note.save()
    return {"note": models.NoteKind(kind).label, "length": len(new)}


TOOLS = (
    create_project,
    log_turnover,
    set_deadline,
    add_credit,
    set_default_shares,
    set_project_note,
)
