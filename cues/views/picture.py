"""The picture page: reels by turnover, and their turnovers.

Logging, editing and deleting a turnover are each one undoable change.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from accounts.models import User
from cues import forms
from cues import models
from cues import timecode
from cues.services import conform
from cues.services import progress
from cues.services import projects as project_service
from cues.services import summaries
from cues.services import turnovers


def _reels(project: models.Project) -> list[models.Picture]:
    """Real reels, without the N/A placeholder."""
    return list(
        project.pictures.exclude(name=project_service.PLACEHOLDER_PICTURE)
        .filter(archived=False)
        .order_by("order", "name")
    )


def _format(frames: int | None, project: models.Project) -> str:
    if frames is None:
        return ""
    return timecode.format_timecode(frames, project.rate)


@require_GET
def picture(request: HttpRequest, project_id: int) -> HttpResponse:
    """A table of reels by turnover, newest first."""
    project = get_object_or_404(models.Project, pk=project_id)
    turnover_list = list(project.turnovers.order_by("-received_date", "-id"))
    current = set(progress.current_picture_versions(project).values())
    versions = {
        (v.picture_id, v.turnover_id): v
        for v in models.PictureVersion.objects.filter(picture__project=project)
    }
    rows = [
        {
            "picture": reel,
            "cells": [
                {
                    "version": versions.get((reel.pk, t.pk)),
                    "current": getattr(
                        versions.get((reel.pk, t.pk)), "pk", None
                    )
                    in current,
                }
                for t in turnover_list
            ],
        }
        for reel in _reels(project)
    ]
    return render(
        request,
        "cues/picture.html",
        {
            "nav": "projects",
            "project": project,
            "summary": summaries.for_project(project),
            "tab": "picture",
            "turnovers": turnover_list,
            "rows": rows,
            "unplaced": turnovers.unplaced(project),
        },
    )


@require_POST
def place_cues(request: HttpRequest, project_id: int) -> HttpResponse:
    """Moves the cues on "N/A" that the page listed to their reels."""
    project = get_object_or_404(models.Project, pk=project_id)
    wanted = set(request.POST.getlist("cue"))
    placements = [
        placement
        for placement in turnovers.unplaced(project)
        if str(placement.cue.pk) in wanted
    ]
    if placements:
        count = turnovers.place(project, placements, _user(request))
        messages.success(
            request,
            f"Placed {count} cue{'' if count == 1 else 's'} on "
            f"{project.reels_word.lower()}. Undo "
            "(Ctrl+Z on the cues page) reverts it.",
        )
    else:
        messages.error(request, "Tick the cues to place.")
    return HttpResponseRedirect(reverse("cues:picture", args=[project.pk]))


@require_http_methods(["GET", "POST"])
def turnover_create(request: HttpRequest, project_id: int) -> HttpResponse:
    """Log a turnover: changed reels pre-filled, plus any new reels."""
    project = get_object_or_404(models.Project, pk=project_id)
    reels = _reels(project)
    current = {
        v.picture_id: v
        for v in models.PictureVersion.objects.filter(
            pk__in=progress.current_picture_versions(project).values()
        )
    }
    initial = []
    for reel in reels:
        version = current.get(reel.pk)
        initial.append(
            {
                "include": True,
                "tc_in": _format(version.tc_in if version else None, project),
                "tc_out": _format(version.tc_out if version else None, project),
            }
        )
    data = request.POST if request.method == "POST" else None
    form = forms.TurnoverForm(data, instance=models.Turnover(project=project))
    existing = forms.ReelUpdateFormSet(
        data,
        initial=initial,
        prefix="reels",
        form_kwargs={"rate": project.rate},
    )
    new = forms.NewReelFormSet(
        data,
        prefix="new",
        form_kwargs={"rate": project.rate, "long_play": project.long_play},
    )
    stale = data is not None and existing.total_form_count() != len(reels)
    if stale:
        messages.error(
            request,
            f"The {project.reels_word.lower()} changed while you were editing; "
            "check them again.",
        )
    elif (
        data is not None
        and form.is_valid()
        and existing.is_valid()
        and new.is_valid()
    ):
        entries = [
            turnovers.ReelEntry(
                reel, reel.name, row["in_frames"], row["out_frames"]
            )
            for reel, row in zip(reels, existing.cleaned_data, strict=True)
            if row.get("include")
        ] + [
            turnovers.ReelEntry(
                None, row["name"], row["in_frames"], row["out_frames"]
            )
            for row in new.cleaned_data
            if row.get("name")
        ]
        try:
            turnover = turnovers.create_turnover(
                project,
                label=form.cleaned_data["label"],
                received_date=form.cleaned_data["received_date"],
                notes=form.cleaned_data["notes"],
                reels=entries,
                user=request.user if isinstance(request.user, User) else None,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            older = summaries.needs_conform_count(project)
            note = ""
            if older:
                verb = "cues are" if older != 1 else "cue is"
                note = f" {older} {verb} now on older picture."
            messages.success(request, f"Logged {turnover}.{note}")
            return _after_change(request, project, turnover)
    return render(
        request,
        "cues/turnover_form.html",
        {
            "nav": "projects",
            "project": project,
            "form": form,
            "rows": list(zip(reels, existing.forms, strict=False)),
            "existing": existing,
            "new": new,
        },
    )


def _user(request: HttpRequest) -> User | None:
    return request.user if isinstance(request.user, User) else None


def _after_change(
    request: HttpRequest, project: models.Project, turnover: models.Turnover
) -> HttpResponse:
    """Back to Picture, or on to the conform assistant if reels moved.

    Cues waiting on "N/A" for reels come first: Picture offers to place
    them.
    """
    if turnovers.unplaced(project):
        return HttpResponseRedirect(reverse("cues:picture", args=[project.pk]))
    if summaries.needs_conform_count(project) or conform.moved_reels(turnover):
        url = reverse("cues:conform", args=[project.pk])
        return HttpResponseRedirect(f"{url}?turnover={turnover.pk}")
    return HttpResponseRedirect(reverse("cues:picture", args=[project.pk]))


@require_http_methods(["GET", "POST"])
def turnover_edit(
    request: HttpRequest, project_id: int, turnover_id: int
) -> HttpResponse:
    """Change a turnover: its details, which reels it covers, their IN/OUT."""
    project = get_object_or_404(models.Project, pk=project_id)
    turnover = get_object_or_404(project.turnovers, pk=turnover_id)
    reels = _reels(project)
    mine = {v.picture_id: v for v in turnover.picture_versions.all()}
    initial = [
        {
            "include": reel.pk in mine,
            "tc_in": _format(mine[reel.pk].tc_in, project)
            if reel.pk in mine
            else "",
            "tc_out": _format(mine[reel.pk].tc_out, project)
            if reel.pk in mine
            else "",
        }
        for reel in reels
    ]
    data = request.POST if request.method == "POST" else None
    form = forms.TurnoverForm(data, instance=turnover)
    existing = forms.ReelUpdateFormSet(
        data,
        initial=initial,
        prefix="reels",
        form_kwargs={"rate": project.rate},
    )
    new = forms.NewReelFormSet(
        data,
        prefix="new",
        form_kwargs={"rate": project.rate, "long_play": project.long_play},
    )
    stale = data is not None and existing.total_form_count() != len(reels)
    if stale:
        messages.error(
            request,
            f"The {project.reels_word.lower()} changed while you were editing; "
            "check them again.",
        )
    elif (
        data is not None
        and form.is_valid()
        and existing.is_valid()
        and new.is_valid()
    ):
        entries = [
            turnovers.ReelEntry(
                reel, reel.name, row["in_frames"], row["out_frames"]
            )
            for reel, row in zip(reels, existing.cleaned_data, strict=True)
            if row.get("include")
        ] + [
            turnovers.ReelEntry(
                None, row["name"], row["in_frames"], row["out_frames"]
            )
            for row in new.cleaned_data
            if row.get("name")
        ]
        try:
            turnovers.update_turnover(
                turnover,
                label=form.cleaned_data["label"],
                received_date=form.cleaned_data["received_date"],
                notes=form.cleaned_data["notes"],
                reels=entries,
                user=_user(request),
            )
        except (ValidationError, turnovers.TurnoverInUseError) as exc:
            form.add_error(
                None, str(exc) if isinstance(exc, ValueError) else exc
            )
        else:
            messages.success(request, f"Saved {turnover}. Undo reverts it.")
            return _after_change(request, project, turnover)
    return render(
        request,
        "cues/turnover_form.html",
        {
            "nav": "projects",
            "project": project,
            "turnover": turnover,
            "form": form,
            "rows": list(zip(reels, existing.forms, strict=False)),
            "existing": existing,
            "new": new,
        },
    )


@require_POST
def turnover_delete(
    request: HttpRequest, project_id: int, turnover_id: int
) -> HttpResponse:
    """Deletes a turnover that no cue was written to."""
    project = get_object_or_404(models.Project, pk=project_id)
    turnover = get_object_or_404(project.turnovers, pk=turnover_id)
    try:
        turnovers.delete_turnover(turnover, _user(request))
    except turnovers.TurnoverInUseError as exc:
        messages.error(request, str(exc))
        return HttpResponseRedirect(
            reverse("cues:turnover_edit", args=[project.pk, turnover.pk])
        )
    messages.success(request, f"Deleted {turnover}. Undo brings it back.")
    return HttpResponseRedirect(reverse("cues:picture", args=[project.pk]))
