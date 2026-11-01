"""A project's Trash: deleted cues, each with Restore (cues.services.trash).

Viewers see the list without the buttons.
"""

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from accounts import roles
from accounts.models import Role
from accounts.models import User
from cues import models
from cues.services import history
from cues.services import summaries
from cues.services import trash

PER_PAGE = 50


@require_GET
def page(request: HttpRequest, project_id: int) -> HttpResponse:
    """Deleted cues, newest first."""
    project = get_object_or_404(models.Project, pk=project_id)
    shown = Paginator(trash.deletions(project), PER_PAGE).get_page(
        request.GET.get("page")
    )
    return render(
        request,
        "cues/trash.html",
        {
            "nav": "projects",
            "project": project,
            "summary": summaries.for_project(project),
            "tab": "trash",
            "page": shown,
            "entries": trash.entries(project, shown),
            "can_edit": roles.at_least(
                getattr(request.user, "role", ""), Role.EDITOR
            ),
        },
    )


@require_POST
def restore(request: HttpRequest, project_id: int, cue_id: int) -> HttpResponse:
    """Brings one cue back, renumbered if asked and its number is taken."""
    project = get_object_or_404(models.Project, pk=project_id)
    user = request.user if isinstance(request.user, User) else None
    try:
        cue = trash.restore(
            project, cue_id, user, renumber=request.POST.get("renumber") == "1"
        )
    except history.UndoError as exc:  # trash.TrashError among them
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Restored {cue.number}.")
    url = reverse("cues:trash", args=[project.pk])
    number = request.POST.get("page", "")
    return HttpResponseRedirect(
        f"{url}?page={number}" if number.isdigit() else url
    )
