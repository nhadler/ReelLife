"""The new cue and new version dialogs (5 "Version rules").

htmx loads each form into the page's <dialog>. Invalid forms re-render
in place (200); a save answers 204 with HX-Trigger events that reload the
grid, refresh the header and close the dialog. "Save and add another"
answers with a fresh form instead, keeping the dialog open.
"""

import json

from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods

from accounts.authz import role_required
from accounts.models import Role
from accounts.models import User
from cues import forms
from cues import models
from cues import timecode
from cues.services import authoring
from cues.services import projects as project_service

# Events the page listens for (grid.js, the header's hx-trigger, dialog.js).
CHANGED = {"cues:reload": True, "cues:changed": True}


def _user(request: HttpRequest) -> User | None:
    return request.user if isinstance(request.user, User) else None


def _typed_in(request: HttpRequest) -> bool:
    """A dialog's address opened directly rather than loaded by htmx.

    The form alone would show unstyled, so the view sends the page it
    belongs to instead.
    """
    return (
        request.method == "GET" and request.headers.get("HX-Request") != "true"
    )


def _closed() -> HttpResponse:
    response = HttpResponse(status=204)
    response["HX-Trigger"] = json.dumps({**CHANGED, "dialog:close": True})
    return response


def _default_picture(project: models.Project) -> models.Picture:
    picture = (
        project.pictures.exclude(name=project_service.PLACEHOLDER_PICTURE)
        .order_by("order", "name")
        .first()
    )
    return picture or project_service.placeholder_picture(project)


def _new_cue_initial(
    project: models.Project, picture: models.Picture
) -> dict[str, object]:
    return {
        "picture": picture,
        "number": authoring.suggested_number(project, picture),
        "status": project_service.default_status(project),
    }


@role_required(Role.EDITOR)
@require_http_methods(["GET", "POST"])
def new_cue(request: HttpRequest, project_id: int) -> HttpResponse:
    """The new cue dialog."""
    project = get_object_or_404(models.Project, pk=project_id)
    if _typed_in(request):  # the grid opens the dialog (grid.js)
        return redirect(reverse("cues:project", args=[project.pk]) + "?new=1")
    added = ""
    if request.method == "POST":
        form = forms.NewCueForm(request.POST, project=project)
        if form.is_valid():
            cue = form.save(commit=False)
            try:
                authoring.create_cue(
                    cue,
                    sub_status=form.cleaned_data["sub_status"],
                    notes={
                        name: form.cleaned_data[name]
                        for name in models.VERSION_NOTE_FIELDS
                    },
                    user=_user(request),
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                if "and_new" not in request.POST:
                    return _closed()
                added = cue.number
                form = forms.NewCueForm(
                    initial=_new_cue_initial(project, cue.picture),
                    project=project,
                )
    else:
        picture = _default_picture(project)
        if "picture" in request.GET:
            picture = get_object_or_404(
                project.pictures, pk=request.GET["picture"]
            )
        form = forms.NewCueForm(
            initial=_new_cue_initial(project, picture), project=project
        )
    response = render(
        request,
        "cues/dialogs/new_cue.html",
        {"project": project, "form": form, "added": added},
    )
    if added:
        response["HX-Trigger"] = json.dumps(CHANGED)
    return response


@role_required(Role.EDITOR)
@require_GET
def suggest_number(request: HttpRequest, project_id: int) -> HttpResponse:
    """The cue number field, refilled when the dialog's picture changes."""
    project = get_object_or_404(models.Project, pk=project_id)
    picture = get_object_or_404(project.pictures, pk=request.GET.get("picture"))
    form = forms.NewCueForm(
        initial={"number": authoring.suggested_number(project, picture)},
        project=project,
    )
    return render(
        request, "cues/includes/field.html", {"field": form["number"]}
    )


def _version_initial(cue: models.Cue) -> dict[str, object]:
    rate = cue.project.rate

    def tc(frames: int | None) -> str:
        return "" if frames is None else timecode.format_timecode(frames, rate)

    return {
        "label": authoring.next_version_label(cue),
        "written_to": cue.picture.current_version(),
        "tc_in": tc(cue.tc_in),
        "tc_out": tc(cue.tc_out),
        "seq_start": tc(cue.seq_start),
        # Approved cues reopen review with a new version; the dialog
        # shows it, so another status can still be picked.
        "status": getattr(authoring.reopened_status(cue), "pk", None),
        "update_cue": True,
        # A new version starts with the current one's notes and sub status.
        **{
            name: getattr(cue.current_version, name)
            for name in (*models.VERSION_NOTE_FIELDS, "sub_status")
            if cue.current_version is not None
        },
    }


@role_required(Role.EDITOR)
@require_http_methods(["GET", "POST"])
def new_version(request: HttpRequest, cue_id: int) -> HttpResponse:
    """The new version dialog for a cue."""
    cue = get_object_or_404(
        models.Cue.objects.select_related("project", "picture"), pk=cue_id
    )
    if _typed_in(request):
        return redirect("cues:cue", cue.pk)
    if request.method == "POST":
        form = forms.NewVersionForm(request.POST, cue=cue)
        if form.is_valid():
            version = form.save(commit=False)
            try:
                authoring.create_version(
                    version,
                    update_cue=form.cleaned_data["update_cue"],
                    status=form.cleaned_data["status"],
                    user=_user(request),
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                if "and_open" in request.POST:
                    response = HttpResponse(status=204)
                    response["HX-Redirect"] = reverse("cues:cue", args=[cue.pk])
                    return response
                return _closed()
    else:
        form = forms.NewVersionForm(initial=_version_initial(cue), cue=cue)
    if cue.duration is not None:
        # Shown, not filled in: a filled DUR would contradict an edited OUT.
        form.fields["duration"].widget.attrs["placeholder"] = (
            timecode.format_duration(cue.duration, cue.project.rate)
        )
    return render(
        request,
        "cues/dialogs/new_version.html",
        {"cue": cue, "project": cue.project, "form": form},
    )
