"""The stats panel beside the grid.

Read by every role. Editors choose which deadline drives the daily pace
here; that choice is a project change like any other, so Undo reverts
it, and the header and panel refresh after it.
"""

import json

from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from accounts import roles
from accounts.authz import role_required
from accounts.models import Role
from accounts.models import User
from cues import models
from cues.services import history
from cues.services import stats


@require_GET
def panel(request: HttpRequest, project_id: int) -> HttpResponse:
    """The panel's contents, loaded into the side panel by grid.js."""
    project = get_object_or_404(models.Project, pk=project_id)
    return render(
        request,
        "cues/stats/panel.html",
        {
            "project": project,
            "stats": stats.for_project(project),
            "can_edit": roles.at_least(
                getattr(request.user, "role", ""), Role.EDITOR
            ),
        },
    )


@role_required(Role.EDITOR)
@require_POST
def choose_deadline(request: HttpRequest, project_id: int) -> HttpResponse:
    """Makes a deadline drive the daily pace."""
    project = get_object_or_404(models.Project, pk=project_id)
    deadline = get_object_or_404(
        project.deadlines, pk=request.POST.get("deadline") or 0
    )
    if project.pace_deadline_id != deadline.pk:
        user = request.user if isinstance(request.user, User) else None
        with history.recording(project, f"Pace by {deadline}", user):
            project.pace_deadline = deadline
            project.save(update_fields=["pace_deadline", "updated_at"])
    response = HttpResponse(status=204)
    response["HX-Trigger"] = json.dumps({"cues:changed": True})
    return response
