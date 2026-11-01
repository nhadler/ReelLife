"""The dashboard: every project with its progress."""

from django.contrib import messages
from django.db import transaction
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from cues import models
from cues.services import project_admin
from cues.services import summaries

SORTS = {
    "changed": "Last Changed",
    "title": "Title",
    "number": "Production #",
    "deadline": "Next Deadline",
}
# Each sort's natural direction: newest change first, the rest A to Z
# and soonest first.
DEFAULT_DIRECTION = {
    "changed": "desc",
    "title": "asc",
    "number": "asc",
    "deadline": "asc",
}


def _sort_value(summary: summaries.ProjectSummary, sort: str) -> str:
    """What a project sorts by, as text; '' for none (no number, deadline)."""
    project = summary.project
    if sort == "title":
        return project.title.lower()
    if sort == "number":
        return project.production_number.lower()
    if sort == "deadline":
        deadline = summary.next_deadline
        return str(deadline.date) if deadline else ""
    return project.updated_at.isoformat()


def _sorted(
    found: list[summaries.ProjectSummary], sort: str, direction: str
) -> list[summaries.ProjectSummary]:
    """In order either way, archived among the rest; blanks always last."""
    valued = [s for s in found if _sort_value(s, sort)]
    blank = [s for s in found if not _sort_value(s, sort)]
    return [
        *sorted(
            valued,
            key=lambda s: _sort_value(s, sort),
            reverse=direction == "desc",
        ),
        *blank,
    ]


def search_text(project: models.Project) -> str:
    """What the Projects search looks in, as the cards' data-search."""
    series = project.series.title if project.series else ""
    return f"{project.title} {project.production_number} {series}".lower()


def matches(project: models.Project, query: str) -> bool:
    """Whether every word of the search is in the project's search text."""
    text = search_text(project)
    return all(word in text for word in query.lower().split())


@require_GET
def dashboard(request: HttpRequest) -> HttpResponse:
    """Lists projects, last changed first, with search, sort and archived.

    The page's script filters and sorts the same list as you type or pick
    a sort; the server does the same for links and browsers without it.
    """
    query = request.GET.get("q", "").strip()
    show_archived = request.GET.get("archived") == "on"
    sort = request.GET.get("sort", "changed")
    if sort not in SORTS:
        sort = "changed"
    # The direction button posts the flipped value last, so it wins.
    directions = request.GET.getlist("dir")
    direction = directions[-1] if directions else DEFAULT_DIRECTION[sort]
    if direction not in {"asc", "desc"}:
        direction = DEFAULT_DIRECTION[sort]
    projects = models.Project.objects.select_related("series").order_by(
        "archived", "-updated_at"
    )
    if not show_archived:
        projects = projects.filter(archived=False)
    # Every project is on the page and the search only hides cards, as
    # projects.js does while typing, so clearing it shows the rest again.
    found = _sorted(
        # Archived projects (years of them, once shown) skip the cue map,
        # a rectangle per cue, which is most of the page's weight.
        [summaries.for_project(p, with_map=not p.archived) for p in projects],
        sort,
        direction,
    )
    hidden = {s.project.pk for s in found if not matches(s.project, query)}
    return render(
        request,
        "cues/dashboard.html",
        {
            "nav": "projects",
            "summaries": found,
            "hidden": hidden,
            "shown": len(found) - len(hidden),
            "total_pace": summaries.total_pace(found),
            "active_count": sum(1 for f in found if not f.project.archived),
            "query": query,
            "show_archived": show_archived,
            "sort": sort,
            "sorts": SORTS,
            "direction": direction,
        },
    )


def _projects(count: int) -> str:
    return "1 project" if count == 1 else f"{count} projects"


@require_POST
def bulk(request: HttpRequest) -> HttpResponse:
    """Archives, brings back or duplicates the ticked projects."""
    ids = [int(pk) for pk in request.POST.getlist("project") if pk.isdigit()]
    projects = list(models.Project.objects.filter(pk__in=ids).order_by("title"))
    action = request.POST.get("action", "")
    if not projects:
        messages.error(request, "Tick the projects first.")
    elif action in {"archive", "unarchive"}:
        with transaction.atomic():
            for project in projects:
                project_admin.set_archived(project, action == "archive")
        done = "Archived" if action == "archive" else "Unarchived"
        messages.success(request, f"{done} {_projects(len(projects))}.")
    elif action == "duplicate":
        with transaction.atomic():
            for project in projects:
                project_admin.duplicate(project, f"{project.title} (copy)")
        messages.success(
            request,
            f"Duplicated {_projects(len(projects))}, without their cues.",
        )
    else:
        messages.error(request, "That isn't something the projects can do.")
    back = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(
        back, allowed_hosts={request.get_host()}
    ):
        back = reverse("cues:dashboard")
    return HttpResponseRedirect(back)
