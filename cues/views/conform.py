"""The conform assistant's page.

Reached after logging a turnover, and from the project bar's "on older
picture" count: first the reels whose IN moved in the latest turnover
(shift their cues by the same amount), then every cue still on older
picture (mark them conformed).
"""

from django.contrib import messages
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
from cues.services import conform
from cues.services import summaries
from cues.services import tools


def _user(request: HttpRequest) -> User | None:
    return request.user if isinstance(request.user, User) else None


def _turnover(
    project: models.Project, request: HttpRequest
) -> models.Turnover | None:
    """The turnover asked for, else the project's latest."""
    turnovers = project.turnovers.order_by("-received_date", "-pk")
    wanted = request.GET.get("turnover") or request.POST.get("turnover")
    if wanted and wanted.isdigit():
        return turnovers.filter(pk=wanted).first()
    return turnovers.first()


def _url(project: models.Project, turnover: models.Turnover | None) -> str:
    url = reverse("cues:conform", args=[project.pk])
    return f"{url}?turnover={turnover.pk}" if turnover else url


@require_GET
def page(request: HttpRequest, project_id: int) -> HttpResponse:
    """Reels that moved, then cues on older picture."""
    project = get_object_or_404(models.Project, pk=project_id)
    turnover = _turnover(project, request)
    moves = conform.moved_reels(turnover) if turnover else []
    return render(
        request,
        "cues/conform.html",
        {
            "nav": "projects",
            "project": project,
            "summary": summaries.for_project(project),
            "tab": "conform",
            "turnover": turnover,
            "moves": [(move, move.label(project.rate)) for move in moves],
            "to_shift": any(move.cues for move in moves),
            "stale": conform.needing_conform(project),
            "can_edit": roles.at_least(
                getattr(request.user, "role", ""), Role.EDITOR
            ),
        },
    )


@require_POST
def shift(request: HttpRequest, project_id: int) -> HttpResponse:
    """Shifts the ticked reels' cues by their reels' moves."""
    project = get_object_or_404(models.Project, pk=project_id)
    turnover = _turnover(project, request)
    ticked = set(request.POST.getlist("reel"))
    moves = [
        move
        for move in (conform.moved_reels(turnover) if turnover else [])
        if str(move.picture.pk) in ticked and move.cues
    ]
    if not moves:
        messages.error(request, "Tick a reel to shift.")
    else:
        try:
            count = conform.shift_reels(project, moves, _user(request))
        except tools.ToolError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f"Shifted {count} cue{'' if count == 1 else 's'}. Undo "
                "(Ctrl+Z on the cues page) reverts it.",
            )
    return HttpResponseRedirect(_url(project, turnover))


@require_POST
def mark(request: HttpRequest, project_id: int) -> HttpResponse:
    """Marks the ticked cues conformed."""
    project = get_object_or_404(models.Project, pk=project_id)
    turnover = _turnover(project, request)
    ticked = set(request.POST.getlist("cue"))
    cues = [
        stale.cue
        for stale in conform.needing_conform(project)
        if str(stale.cue.pk) in ticked
    ]
    if not cues:
        messages.error(request, "Tick the cues to mark conformed.")
    else:
        new_version = request.POST.get("how") == "new"
        conform.mark_conformed(
            project, cues, new_version=new_version, user=_user(request)
        )
        noun = "cue" if len(cues) == 1 else "cues"
        how = "with new versions" if new_version else "in place"
        messages.success(request, f"Marked {len(cues)} {noun} conformed {how}.")
    return HttpResponseRedirect(_url(project, turnover))
