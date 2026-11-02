"""A project's Activity: every change set, with Undo and Redo.

Newest first, a page at a time. Any change set can be undone from here,
not only the latest, when nothing it touched was changed afterwards
(history.undo checks, inside its transaction); undoing an undo is Redo.
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
from django.utils.text import capfirst
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from accounts import roles
from accounts.models import Role
from accounts.models import User
from cues import models
from cues.services import activity
from cues.services import history
from cues.services import summaries

PER_PAGE = 50


def _user(request: HttpRequest) -> User | None:
    return request.user if isinstance(request.user, User) else None


@require_GET
def page(request: HttpRequest, project_id: int) -> HttpResponse:
    """The project's change sets, newest first."""
    project = get_object_or_404(models.Project, pk=project_id)
    paginator = Paginator(
        project.change_sets.select_related("user").order_by(
            "-created_at", "-id"
        ),
        PER_PAGE,
    )
    shown = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "cues/activity.html",
        {
            "nav": "projects",
            "project": project,
            "summary": summaries.for_project(project),
            "tab": "activity",
            "page": shown,
            "entries": activity.entries(list(shown)),
            "can_edit": roles.at_least(
                getattr(request.user, "role", ""), Role.EDITOR
            ),
        },
    )


@require_POST
def undo(
    request: HttpRequest, project_id: int, change_set_id: int
) -> HttpResponse:
    """Undoes one change set (or, for an undo, redoes what it undid)."""
    change_set = get_object_or_404(
        models.ChangeSet, pk=change_set_id, project_id=project_id
    )
    # An undo of an undo re-applies the original: say what, not "Undo: …".
    original, depth = change_set, 0
    while original.undo_of is not None:
        original, depth = original.undo_of, depth + 1
    try:
        history.undo(change_set, _user(request))
    except history.UndoError as exc:
        messages.error(request, capfirst(str(exc)))
    else:
        verb = "Redid" if depth % 2 else "Undid"
        messages.success(request, f"{verb} {original.label}.")
    url = reverse("cues:activity", args=[project_id])
    number = request.POST.get("page", "")
    if number.isdigit():
        return HttpResponseRedirect(f"{url}?page={int(number)}")
    return HttpResponseRedirect(url)
