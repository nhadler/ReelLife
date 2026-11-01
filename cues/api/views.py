"""HTTP endpoints for the grid: load, edit a cell, views, undo."""

from collections.abc import Callable
import json
from typing import Any, TypeVar

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from accounts import roles
from accounts.models import Role
from accounts.models import User
from cues import models
from cues.api import edits
from cues.api import grid
from cues.exporters import cue_json
from cues.services import history
from cues.services import summaries
from cues.services import view_presets


def _user(request: HttpRequest) -> User | None:
    return request.user if isinstance(request.user, User) else None


def _write_rate(group: str, request: HttpRequest) -> str:
    del group, request
    return str(settings.RATELIMIT_API_WRITES)


def _user_key(group: str, request: HttpRequest) -> str:
    del group
    return str(request.user.pk)


ViewT = TypeVar("ViewT", bound=Callable[..., Any])

_limit_writes: Callable[[ViewT], ViewT] = ratelimit(
    group="api-writes", key=_user_key, rate=_write_rate, block=True
)


@require_GET
def grid_data(request: HttpRequest, project_id: int) -> JsonResponse:
    """Everything the grid needs to render a project."""
    project = get_object_or_404(models.Project, pk=project_id)
    can_edit = roles.at_least(getattr(request.user, "role", ""), Role.EDITOR)
    return JsonResponse(grid.payload(project, can_edit=can_edit))


def _error(message: str, status: int, **extra: Any) -> JsonResponse:
    return JsonResponse({"error": message, **extra}, status=status)


@_limit_writes
@require_http_methods(["PATCH"])
def edit_cue(request: HttpRequest, cue_id: int) -> JsonResponse:
    """Edits one cell: body {field, value, updated_at}.

    Returns the updated row; 409 if someone else changed the cue since
    the grid loaded it; 400 for invalid values. Errors include the row as
    it stands, so the grid can show it again.
    """
    cue = get_object_or_404(
        models.Cue.objects.select_related("project"), pk=cue_id
    )
    try:
        body = json.loads(request.body or b"{}")
        field, value = body["field"], body.get("value")
        seen = body["updated_at"]
    except (ValueError, KeyError, TypeError):
        return _error("Send JSON with field, value and updated_at.", 400)
    error: tuple[str, int] | None = None
    try:
        # One transaction from the check to the save: SQLite's IMMEDIATE
        # mode takes the writer lock here, so no other edit can land in
        # between and be overwritten.
        with transaction.atomic():
            cue = models.Cue.objects.select_related("project").get(pk=cue.pk)
            if seen != cue.updated_at.isoformat():
                error = (
                    "Someone else changed this cue; showing their version.",
                    409,
                )
            else:
                edits.apply(cue, str(field), value, _user(request))
    except edits.EditError as exc:
        error = (str(exc), 400)
    row = grid.row(
        grid.cues_queryset(cue.project).get(pk=cue.pk),
        grid.Context.build(cue.project),
    )
    if error is not None:
        return _error(error[0], error[1], row=row)
    # The project header too, so the page needn't ask for it again.
    return JsonResponse(
        {"row": row, "header": _header_html(request, cue.project)}
    )


MAX_BATCH = 2000


@_limit_writes
@require_POST
def edit_cues(request: HttpRequest, project_id: int) -> JsonResponse:
    """Batch edit: body {ids, field, value}; one change set.

    Returns the updated rows; 400 (with nothing changed) for an invalid
    value, a field that can't be batch edited, or ids not in the project.
    """
    project = get_object_or_404(models.Project, pk=project_id)
    try:
        body = json.loads(request.body or b"{}")
        ids = [int(pk) for pk in body["ids"]]
        field, value = str(body["field"]), body.get("value")
    except (ValueError, KeyError, TypeError):
        return _error("Send JSON with ids, field and value.", 400)
    if not 0 < len(set(ids)) <= MAX_BATCH:
        return _error("Pick between one and 2,000 cues.", 400)
    cues = list(
        project.cues.filter(pk__in=ids).select_related(
            "project", "current_version", "picture"
        )
    )
    if len(cues) != len(set(ids)):
        return _error("Some of those cues aren't in this project.", 400)
    try:
        edits.apply_many(cues, field, value, _user(request))
    except edits.EditError as exc:
        return _error(str(exc), 400)
    ctx = grid.Context.build(project)
    fresh = grid.cues_queryset(project).filter(pk__in=ids)
    return JsonResponse({"rows": [grid.row(cue, ctx) for cue in fresh]})


def _body(request: HttpRequest) -> dict[str, Any]:
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise view_presets.ViewError("Send a JSON object.")
    return body


@_limit_writes
@require_POST
def create_view(request: HttpRequest, project_id: int) -> JsonResponse:
    """Adds a view: body {name, columns, sort?} (also for Duplicate)."""
    project = get_object_or_404(models.Project, pk=project_id)
    try:
        body = _body(request)
        view = view_presets.create(
            project, body.get("name"), body.get("columns"), body.get("sort")
        )
    except view_presets.ViewError as exc:
        return _error(str(exc), 400)
    return JsonResponse({"view": grid.view_json(view)}, status=201)


@_limit_writes
@require_http_methods(["PATCH", "DELETE"])
def change_view(
    request: HttpRequest, project_id: int, view_id: int
) -> JsonResponse:
    """Changes or deletes a view; answers with the project's views.

    PATCH {name?, columns?, sort?} changes it, copying a seeded view into
    the project first; DELETE removes the project's own view, or resets a
    changed seeded one.
    """
    project = get_object_or_404(models.Project, pk=project_id)
    view = get_object_or_404(models.View, pk=view_id)
    try:
        if request.method == "DELETE":
            view_presets.delete(project, view)
            saved = None
        else:
            saved = view_presets.update(project, view, _body(request))
    except view_presets.ViewError as exc:
        return _error(str(exc), 400)
    return JsonResponse(
        {
            "view": grid.view_json(saved) if saved else None,
            "views": grid.views_for(project),
        }
    )


@_limit_writes
@require_POST
def undo_last(request: HttpRequest, project_id: int) -> JsonResponse:
    """Undoes the signed-in user's latest change in the project (Ctrl+Z)."""
    project = get_object_or_404(models.Project, pk=project_id)
    latest = (
        project.change_sets.filter(
            user=_user(request), undone_at__isnull=True, undo_of__isnull=True
        )
        .order_by("-created_at", "-id")
        .first()
    )
    if latest is None:
        return _error("Nothing to undo.", 409)
    try:
        history.undo(latest, _user(request))
    except history.UndoError as exc:
        return _error(str(exc), 409)
    return JsonResponse({"undone": latest.label})


def _header_html(request: HttpRequest, project: models.Project) -> str:
    return render_to_string(
        "cues/includes/project_header.html",
        {
            "project": project,
            "summary": summaries.for_project(project),
            "tab": "cues",
        },
        request=request,
    )


@require_GET
def project_header(request: HttpRequest, project_id: int) -> HttpResponse:
    """The project header, re-fetched by htmx after changes."""
    project = get_object_or_404(models.Project, pk=project_id)
    return HttpResponse(_header_html(request, project))


MAX_JSON_CUES = 500


@require_GET
def cues_json(request: HttpRequest) -> JsonResponse:
    """Copy JSON: the cue data macros read, for cues of one project.

    Query: ids (comma-separated cue ids, in order) and, for a single cue,
    version (describe that version instead of the current one).
    """
    ids = [
        int(part)
        for part in request.GET.get("ids", "").split(",")
        if part.strip().isdigit()
    ]
    if not ids or len(ids) > MAX_JSON_CUES:
        return _error(f"Give between 1 and {MAX_JSON_CUES} cue ids.", 400)
    found = models.Cue.objects.select_related(
        "project__series",
        "picture",
        "status",
        "current_version__written_to__picture",
        "current_version__written_to__turnover",
    ).in_bulk(ids)
    cues = [found[pk] for pk in ids if pk in found]
    projects = {cue.project_id for cue in cues}
    if len(projects) != 1:
        return _error("Give cues from one project.", 400)
    pairs = [(cue, cue.current_version) for cue in cues]
    if "version" in request.GET:
        if len(cues) != 1:
            return _error("A version needs exactly one cue.", 400)
        version = get_object_or_404(
            models.CueVersion.objects.select_related(
                "written_to__picture", "written_to__turnover"
            ),
            pk=request.GET["version"],
            cue=cues[0],
        )
        pairs = [(cues[0], version)]
    return JsonResponse(
        cue_json.entries(pairs, cues[0].project),
        safe=False,
        json_dumps_params={"ensure_ascii": False},
    )
