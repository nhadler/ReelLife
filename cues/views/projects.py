"""Creating a project and its main page (7.4)."""

from typing import Any

from django.contrib import messages
from django.db import transaction
from django.http import FileResponse
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods

from accounts import roles
from accounts.models import Role
from accounts.models import User
from cues import columns
from cues import day_off_forms
from cues import forms
from cues import models
from cues import timecode
from cues.api import edits
from cues.services import projects as project_service
from cues.services import summaries
from cues.services import turnovers

# The View menu's row sizes; grid.js maps each to a height (1 to 5).
ROW_SIZES = (
    ("tiny", "Tiny"),
    ("small", "Small"),
    ("medium", "Medium"),
    ("large", "Large"),
    ("auto", "Auto"),
)


@require_http_methods(["GET", "POST"])
def project_create(request: HttpRequest) -> HttpResponse:
    """New project with its deadlines (reels come with the first turnover)."""
    data = request.POST if request.method == "POST" else None
    form = forms.ProjectForm(data, request.FILES or None)
    deadlines = forms.DeadlinesForm(data, prefix="deadlines")
    days_off = day_off_forms.days_off_formset(data)
    first = forms.FirstTurnoverForm(data, prefix="turnover")
    project_ok = data is not None and form.is_valid()
    # Reel timecodes read in the frame rate chosen on the same form.
    reels = forms.NewReelFormSet(
        data,
        prefix="reels",
        form_kwargs={
            "rate": _posted_rate(form) if project_ok else None,
            # One unnamed picture (ReelRowForm), chosen on this same form.
            "long_play": data is not None
            and data.get("picture_type")
            == models.Project.PictureType.LONG_PLAY,
        },
    )
    wanted = (
        project_ok
        and first.is_valid()
        and reels.is_valid()
        and first.check_wanted(reels)
    )
    if (
        project_ok
        and deadlines.is_valid()
        and day_off_forms.days_off_ok(days_off)
        and first.is_valid()  # check_wanted adds errors here
        and reels.is_valid()
    ):
        with transaction.atomic():
            day_off_forms.save_days_off(days_off)
            project = project_service.create_project(**form.model_fields())
            for deadline in deadlines.deadlines():
                project.deadlines.create(**deadline)
            if form.cleaned_data["thumbnail_upload"]:
                project_service.set_thumbnail(
                    project, form.cleaned_data["thumbnail_upload"]
                )
            turnover = _first_turnover(request, project, first, reels, wanted)
        messages.success(
            request,
            f"Created {project} with turnover {turnover}."
            if turnover
            else f"Created {project}. Log the first turnover on the Picture "
            "page to place its cues.",
        )
        return HttpResponseRedirect(reverse("cues:project", args=[project.pk]))
    return render(
        request,
        "cues/project_create.html",
        {
            "nav": "projects",
            "form": form,
            "deadlines": deadlines,
            "days_off": days_off,
            "first": first,
            "reels": reels,
        },
    )


def _posted_rate(form: forms.ProjectForm) -> timecode.Rate:
    """The frame rate the New Project form chose."""
    return timecode.rate_for(
        form.cleaned_data["frame_rate"],
        drop_frame=bool(form.cleaned_data.get("drop_frame")),
    )


def _first_turnover(
    request: HttpRequest,
    project: models.Project,
    first: forms.FirstTurnoverForm,
    reels: Any,
    wanted: bool,
) -> models.Turnover | None:
    """Logs New Project's first turnover, when one was filled in."""
    if not wanted:
        return None
    return turnovers.create_turnover(
        project,
        label=first.cleaned_data["label"],
        received_date=first.cleaned_data["received_date"],
        reels=[
            turnovers.ReelEntry(
                None, row["name"], row["in_frames"], row["out_frames"]
            )
            for row in reels.cleaned_data
            if row.get("name")
        ],
        user=request.user if isinstance(request.user, User) else None,
    )


@require_GET
def project_detail(request: HttpRequest, project_id: int) -> HttpResponse:
    """The cue overview: header, AG Grid and view tabs."""
    project = get_object_or_404(models.Project, pk=project_id)
    config = {
        "projectId": project.pk,
        "frameRate": project.frame_rate,
        # Frames per second of running time for durations, as the server
        # counts them (timecode.seconds): actual for drop-frame, else
        # nominal, so selection totals match the cue sheet.
        "durationFps": float(timecode.duration_fps(project.rate)),
        "lanes": project.lanes,
        # Reel, Act, Clip or Picture, as the project's picture type has it.
        "reelWord": project.reel_word,
        "reelsWord": project.reels_word,
        "dataUrl": reverse("cues:api_grid", args=[project.pk]),
        "editUrl": reverse("cues:api_cue", args=[0]),
        "batchUrl": reverse("cues:api_cues_edit", args=[project.pk]),
        "notBatch": sorted(edits.NOT_BATCH),
        # What the find field matches: the current version's, as rows
        # carry them.
        "searchFields": ["number", "title", *columns.SEARCHED_NOTES],
        "undoUrl": reverse("cues:api_undo", args=[project.pk]),
        "viewsUrl": reverse("cues:api_views", args=[project.pk]),
        "notesPanelUrl": reverse("cues:project_notes_panel", args=[project.pk]),
        "viewUrl": reverse("cues:api_view", args=[project.pk, 0]),
        "newVersionUrl": reverse("cues:new_version", args=[0]),
        "detailUrl": reverse("cues:cue", args=[0]),
        "exportUrl": reverse("cues:export", args=[project.pk]),
        "toolUrl": reverse("cues:tool", args=[project.pk, "tool"]),
        "jsonUrl": reverse("cues:api_cues_json"),
        "canEdit": roles.at_least(
            getattr(request.user, "role", ""), Role.EDITOR
        ),
        "csrfToken": get_token(request),
        # AG Grid injects <style> elements; the CSP allows this nonce.
        "styleNonce": str(getattr(request, "csp_nonce", "")),
    }
    return render(
        request,
        "cues/project_detail.html",
        {
            "nav": "projects",
            "project": project,
            "summary": summaries.for_project(project),
            "tab": "cues",
            "grid_config": config,
            "row_sizes": ROW_SIZES,
        },
    )


@require_GET
def thumbnail(request: HttpRequest, project_id: int, name: str) -> FileResponse:
    """Serves a project's processed thumbnail to signed-in users.

    The file name is part of the URL and changes with every upload, so the
    response can be cached for good.
    """
    del request
    project = get_object_or_404(models.Project, pk=project_id)
    if not project.thumbnail or name != project.thumbnail_name:
        raise Http404
    response = FileResponse(
        project.thumbnail.open("rb"), content_type="image/webp"
    )
    response["Cache-Control"] = "private, max-age=31536000, immutable"
    return response
