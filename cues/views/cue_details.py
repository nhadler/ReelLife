"""The cue details page.

The cue's versions down the left, the cue's fields and the chosen
version's fields in the middle, and the change history on the right.
There is no Save button: each field posts itself when it changes
(`edits.apply` / `edits.apply_version`, the same rules and undo as the
grid) and the response swaps in the saved field. A hidden `updated_at`
travels with every save, so an edit made elsewhere in the meantime is
refused rather than overwritten.
"""

import json
from typing import Any
import urllib.parse

from django import forms as django_forms
from django.contrib import messages
from django.db.models import Q
from django.http import FileResponse
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from accounts import roles
from accounts.authz import role_required
from accounts.models import Role
from accounts.models import User
from cues import forms
from cues import models
from cues import timecode
from cues.api import edits
from cues.api import grid
from cues.services import authoring
from cues.services import cue_history
from cues.services import history
from cues.services import progress
from cues.services import thumbnails
from cues.views import review as review_views
from cues.views import shares as share_views
from cues.views.imports import _limit as _limit_uploads

STALE = "This cue was changed somewhere else. Reload the page to see it."
NEWER_FIRST = (
    "Something changed after that, so it can't be reverted on its own. "
    "Revert the newer changes first."
)
# Fields whose saved value can change what other fields show.
_TIMING = ("tc_in", "tc_out", "duration")
_CHECKBOXES = frozenset({"plays_as_source", "interrupted_use", "active"})


def _user(request: HttpRequest) -> User | None:
    return request.user if isinstance(request.user, User) else None


def _can_edit(request: HttpRequest) -> bool:
    return roles.at_least(getattr(request.user, "role", ""), Role.EDITOR)


def _cue(cue_id: int) -> models.Cue:
    return get_object_or_404(
        models.Cue.objects.select_related(
            "project",
            "picture",
            "status",
            "current_version__written_to__turnover",
        ),
        pk=cue_id,
    )


# The department groups on the details page, as the views group them.
DEPARTMENTS = (
    (
        "Orchestration",
        (
            "orchestration_status",
            "orchestration_version",
            "score",
            "parts",
            "printed",
            "orchestration_notes",
        ),
    ),
    ("Recording", ("recording_status", "clicks", "prelays", "recording_notes")),
    ("Mix", ("mix_status", "mix_version", "mix_notes")),
    ("Music Edit & Licensing", ("music_edit_status", "licensing_status")),
)


def _department_groups(
    form: forms.CueDetailsForm,
) -> list[tuple[str, list[Any], bool]]:
    """Each group's name, fields, and whether any field has a value.

    An empty group folds shut on the page.
    """
    groups = []
    for name, fields in DEPARTMENTS:
        bound = [form[f] for f in fields]
        filled = any(b.value() not in (None, "", False) for b in bound)
        groups.append((name, bound, filled))
    return groups


def _cue_form(request: HttpRequest, cue: models.Cue) -> forms.CueDetailsForm:
    return forms.CueDetailsForm(
        cue=cue,
        url_for=lambda name: reverse("cues:cue_field", args=[cue.pk, name]),
        can_edit=_can_edit(request),
    )


def _version_form(
    request: HttpRequest, version: models.CueVersion
) -> forms.VersionDetailsForm:
    return forms.VersionDetailsForm(
        version=version,
        url_for=lambda name: reverse(
            "cues:version_field", args=[version.pk, name]
        ),
        can_edit=_can_edit(request),
    )


def _versions(cue: models.Cue) -> list[models.CueVersion]:
    return list(
        cue.versions.select_related("written_to__turnover").order_by(
            "-created_at", "-id"
        )
    )


def _web_link(text: str) -> str:
    """The text if it's an http(s) URL (safe to link to), else ""."""
    parts = urllib.parse.urlsplit(text.strip())
    return (
        text.strip()
        if parts.scheme in ("http", "https") and parts.netloc
        else ""
    )


Neighbour = tuple[int, str] | None  # (id, number)


def _neighbours(cue: models.Cue) -> tuple[Neighbour, Neighbour]:
    """The previous and next cue in grid order (active cues, plus this)."""
    rows = list(
        cue.project.cues.filter(Q(active=True) | Q(pk=cue.pk))
        .order_by("picture__order", "picture__name", "tc_in", "number")
        .values_list("pk", "number")
    )
    at = [pk for pk, _ in rows].index(cue.pk)
    previous = rows[at - 1] if at > 0 else None
    following = rows[at + 1] if at + 1 < len(rows) else None
    return previous, following


def _switcher(
    project: models.Project,
) -> list[tuple[models.Picture, list[Any]]]:
    """Every cue by reel, for the cue switcher."""
    reels: dict[int, tuple[models.Picture, list[Any]]] = {}
    cues = project.cues.select_related("picture", "status").order_by(
        "picture__order", "picture__name", "tc_in", "number"
    )
    for cue in cues:
        reels.setdefault(cue.picture_id, (cue.picture, []))[1].append(cue)
    return list(reels.values())


def _compare(cue: models.Cue) -> dict[str, Any]:
    """The conform strip: the current version against today's picture."""
    version = cue.current_version
    current = cue.picture.current_version()
    rate = cue.project.rate
    result = progress.conform(
        written_to_id=version.written_to_id if version else None,
        current_picture_version_id=current.pk if current else None,
        cue_in=cue.tc_in,
        cue_out=cue.tc_out,
        version_in=version.tc_in if version else None,
        version_out=version.tc_out if version else None,
    )
    return {
        "version": version,
        "current": current,
        "needs_conform": result.needs_conform,
        "start_offset": None
        if result.start_offset is None
        else timecode.format_offset(result.start_offset, rate),
        "duration_difference": None
        if result.duration_difference is None
        else timecode.format_offset(result.duration_difference, rate),
    }


@require_GET
def cue_detail(request: HttpRequest, cue_id: int) -> HttpResponse:
    """The cue details page, showing one version (the current by default)."""
    cue = _cue(cue_id)
    versions = _versions(cue)
    selected = cue.current_version
    if "version" in request.GET:
        selected = next(
            (v for v in versions if str(v.pk) == request.GET["version"]), None
        )
        if selected is None:
            raise Http404
    elif selected is None and versions:
        selected = versions[0]
    previous, following = _neighbours(cue)
    cue_form = _cue_form(request, cue)
    return render(
        request,
        "cues/cue_detail.html",
        {
            "nav": "projects",
            "project": cue.project,
            "cue": cue,
            "versions": versions,
            "selected": selected,
            "cue_form": cue_form,
            "department_groups": _department_groups(cue_form),
            "version_form": _version_form(request, selected)
            if selected
            else None,
            "file_url": _web_link(selected.file_link) if selected else "",
            "previous": previous,
            "following": following,
            "switcher": _switcher(cue.project),
            "compare": _compare(cue),
            "thumbnail_url": grid.cue_thumbnail_url(cue),
            "can_edit": _can_edit(request),
            **share_views.shares_context(cue),
            **review_views.panel_context(request, cue, selected),
            "page_config": {
                "projectUrl": reverse("cues:project", args=[cue.project_id]),
                # Copy JSON describes the version on screen.
                "jsonUrl": f"{reverse('cues:api_cues_json')}?ids={cue.pk}"
                + (f"&version={selected.pk}" if selected else ""),
            },
        },
    )


def _saved(
    request: HttpRequest,
    cue: models.Cue,
    rows: list[tuple[Any, str]],
    *,
    error: str = "",
    version: models.CueVersion | None = None,
) -> HttpResponse:
    """The re-rendered field(s), plus the page bits a save can change.

    Args:
        request: The request.
        cue: The cue, fresh from the database.
        rows: (bound field, oob) pairs: the first replaces the field that
            posted; the rest are swapped out of band.
        error: Shown under the first field; nothing was saved.
        version: The version being shown, after a version field save:
            the version list is refreshed too (its label may change).
    """
    response = render(
        request,
        "cues/details/saved.html",
        {
            "cue": cue,
            "rows": rows,
            "error": error,
            "versions": _versions(cue) if version else None,
            "selected": version,
            "can_edit": True,
        },
    )
    if not error:
        response["HX-Trigger"] = json.dumps({"cues:changed": True})
    return response


def _is_stale(request: HttpRequest, cue: models.Cue) -> bool:
    return request.POST.get("updated_at") != cue.updated_at.isoformat()


@role_required(Role.EDITOR)
@require_POST
def cue_field(request: HttpRequest, cue_id: int, field: str) -> HttpResponse:
    """Saves one cue field."""
    cue = _cue(cue_id)
    if field not in _cue_form(request, cue).fields:
        raise Http404
    posted: Any = (
        field in request.POST
        if field in _CHECKBOXES
        else request.POST.get(field, "")
    )
    error = ""
    if _is_stale(request, cue):
        error = STALE
    else:
        try:
            edits.apply(cue, field, posted, _user(request))
        except edits.EditError as exc:
            error = str(exc)
    cue = _cue(cue_id)
    form = _cue_form(request, cue)
    if error:
        form.initial[field] = posted
        return _saved(request, cue, [(form[field], "")], error=error)
    rows = [(form[field], "")]
    if field in _TIMING:
        rows += [(form[name], "true") for name in _TIMING if name != field]
    return _saved(request, cue, rows)


@role_required(Role.EDITOR)
@require_POST
def version_field(
    request: HttpRequest, version_id: int, field: str
) -> HttpResponse:
    """Saves one field of a version."""
    version = get_object_or_404(models.CueVersion, pk=version_id)
    if field not in edits.VERSION_FIELDS:
        raise Http404
    posted: str | int = request.POST.get(f"version-{field}", "")
    if field == "sub_status" and str(posted).isdigit():
        # The page's select posts ids; text names a new one (grid only).
        posted = int(posted)
    error = ""
    if _is_stale(request, version.cue):
        error = STALE
    else:
        try:
            edits.apply_version(version, field, posted, _user(request))
        except edits.EditError as exc:
            error = str(exc)
    version = get_object_or_404(models.CueVersion, pk=version_id)
    form = _version_form(request, version)
    if error:
        form.initial[field] = posted
    return _saved(
        request,
        _cue(version.cue_id),
        [(form[field], "")],
        error=error,
        version=version,
    )


@require_GET
def cue_compare(request: HttpRequest, cue_id: int) -> HttpResponse:
    """The conform strip, refreshed after each save."""
    cue = _cue(cue_id)
    return render(
        request,
        "cues/details/compare.html",
        {"cue": cue, "compare": _compare(cue)},
    )


def _history(
    request: HttpRequest, cue: models.Cue, error: str = ""
) -> HttpResponse:
    return render(
        request,
        "cues/details/history.html",
        {
            "cue": cue,
            "entries": cue_history.entries(cue),
            "can_edit": _can_edit(request),
            "error": error,
        },
    )


@require_GET
def cue_history_panel(request: HttpRequest, cue_id: int) -> HttpResponse:
    """The history panel, refreshed after each save."""
    return _history(request, _cue(cue_id))


class ThumbnailForm(django_forms.Form):
    """The cue page's thumbnail upload (the same rules as a project's)."""

    thumbnail = django_forms.FileField(required=False)


@role_required(Role.EDITOR)
@require_POST
@_limit_uploads
def cue_thumbnail_upload(request: HttpRequest, cue_id: int) -> HttpResponse:
    """Sets or removes a cue's thumbnail, then shows the cue again."""
    cue = _cue(cue_id)
    back = HttpResponseRedirect(reverse("cues:cue", args=[cue.pk]))
    if "remove" in request.POST:
        if cue.thumbnail:
            authoring.set_thumbnail(cue, None, user=_user(request))
        return back
    form = ThumbnailForm(request.POST, request.FILES)
    upload = form.cleaned_data["thumbnail"] if form.is_valid() else None
    if not upload:
        messages.error(request, "Choose an image to upload.")
        return back
    try:
        content = thumbnails.save_upload(upload)
    except thumbnails.ThumbnailError as exc:
        messages.error(request, str(exc))
        return back
    authoring.set_thumbnail(cue, content, user=_user(request))
    return back


@require_GET
def cue_thumbnail(request: HttpRequest, cue_id: int, name: str) -> FileResponse:
    """Serves a cue's thumbnail to signed-in users.

    The name changes with each upload, so the response is cached for good.
    """
    del request
    cue = get_object_or_404(models.Cue, pk=cue_id)
    if not cue.thumbnail or name != cue.thumbnail_name:
        raise Http404
    response = FileResponse(cue.thumbnail.open("rb"), content_type="image/webp")
    response["Cache-Control"] = "private, max-age=31536000, immutable"
    return response


def _refresh() -> HttpResponse:
    response = HttpResponse(status=204)
    response["HX-Refresh"] = "true"
    return response


def _redirect(url: str) -> HttpResponse:
    response = HttpResponse(status=204)
    response["HX-Redirect"] = url
    return response


@role_required(Role.EDITOR)
@require_POST
def revert(
    request: HttpRequest, cue_id: int, change_set_id: int
) -> HttpResponse:
    """Undoes one history entry's change set."""
    cue = _cue(cue_id)
    change_set = get_object_or_404(
        models.ChangeSet, pk=change_set_id, project=cue.project
    )
    try:
        history.undo(change_set, _user(request))
    except history.ChangedSinceError:
        return _history(request, cue, NEWER_FIRST)
    except history.UndoError as exc:
        return _history(request, cue, str(exc))
    if not models.Cue.objects.filter(pk=cue_id).exists():
        return _redirect(reverse("cues:project", args=[cue.project_id]))
    return _refresh()


@role_required(Role.EDITOR)
@require_POST
def make_current(request: HttpRequest, version_id: int) -> HttpResponse:
    """Makes a version the cue's current one."""
    version = get_object_or_404(models.CueVersion, pk=version_id)
    authoring.make_current(version, user=_user(request))
    return _refresh()


@role_required(Role.EDITOR)
@require_POST
def delete_version(request: HttpRequest, version_id: int) -> HttpResponse:
    """Deletes a version and shows the cue's (new) current one."""
    version = get_object_or_404(models.CueVersion, pk=version_id)
    cue_id = version.cue_id
    authoring.delete_version(version, user=_user(request))
    return _redirect(reverse("cues:cue", args=[cue_id]))


@role_required(Role.EDITOR)
@require_POST
def delete_cue(request: HttpRequest, cue_id: int) -> HttpResponse:
    """Deletes the cue and returns to the project."""
    cue = _cue(cue_id)
    project_id = cue.project_id
    authoring.delete_cue(cue, user=_user(request))
    return _redirect(reverse("cues:project", args=[project_id]))
