"""The import dialog, in two steps.

Step one picks the source, its options and the file; step two shows the
preview: one row per cue with an include box, NEW / UPDATE / SKIP, and
for updates the old and new timecodes (with the conform offset when the
cue was written to older picture). The file is posted again with every
request (see cues/services/imports.py), and the preview's fingerprint
must still match when Import is pressed.
"""

from collections.abc import Callable
import dataclasses
import hashlib
import json
from typing import Any, TypeVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from accounts.authz import role_required
from accounts.models import Role
from accounts.models import User
from cues import models
from cues import timecode
from cues import transfer_forms
from cues.importers import apply
from cues.importers import base
from cues.importers import merge
from cues.importers import tabular
from cues.services import imports

FIELD_LABELS = {
    "number": "Cue #",
    "title": "Title",
    "tc_in": "IN",
    "tc_out": "OUT",
    "duration": "DUR",
    "seq_start": "Seq. start",
    "status": "Status",
    "notes": "Notes",
    "spotting_notes": "Spotting notes",
    "version": "Version",
    "usage": "Usage",
    "cue_type": "Cue type",
    "temp": "Temp",
    "key": "Key",
    "bpm": "BPM",
    "meter": "Meter",
    "in_note": "IN note",
    "out_note": "OUT note",
    "scene": "Scene",
    "scene_description": "Scene description",
    "priority": "Priority",
    "difficulty": "Difficulty",
    "artist": "Artist",
    "cue_sheet_title": "Cue sheet title",
}
CHANGED_SINCE = (
    "The project changed after this preview was made. Check the preview "
    "below and import again."
)


def _rate(group: str, request: HttpRequest) -> str:
    del group, request
    return str(settings.RATELIMIT_IMPORTS)


def _user_key(group: str, request: HttpRequest) -> str:
    del group
    return str(request.user.pk)


ViewT = TypeVar("ViewT", bound=Callable[..., Any])

_limit: Callable[[ViewT], ViewT] = ratelimit(
    group="imports", key=_user_key, rate=_rate, block=True
)


def _user(request: HttpRequest) -> User | None:
    return request.user if isinstance(request.user, User) else None


@dataclasses.dataclass
class _Planned:
    """One parse and plan of the posted file."""

    kind: imports.Kind
    source: imports.Source
    parsed: base.ParsedFile
    preview: merge.Preview


def _plan(form: transfer_forms.ImportForm) -> _Planned:
    """Parses and plans the posted file.

    Raises:
        base.ImportFormatError: Unreadable file or wrong frame rate.
        ValueError: Unusable options.
    """
    data, kind = form.data_and_kind()
    source = form.source(kind)
    parsed = imports.parse(form.project, data, source)
    preview = imports.plan(form.project, parsed, form.options(kind))
    return _Planned(kind, source, parsed, preview)


def _numbers_key(preview: merge.Preview) -> str:
    numbers = [change.row.number for change in preview.changes]
    return hashlib.sha256(json.dumps(numbers).encode()).hexdigest()


def _tc(frames: int | None, rate: timecode.Rate) -> str:
    return "" if frames is None else timecode.format_timecode(frames, rate)


def _rows(
    project: models.Project, preview: merge.Preview, included: set[int]
) -> list[dict[str, Any]]:
    """The preview table's rows, ready to show."""
    rate = project.rate
    pictures = {
        pk: project.picture_title(name)
        for pk, name in project.pictures.values_list("id", "name")
    }
    rows = []
    for index, change in enumerate(preview.changes):
        row = change.row
        update = change.action == merge.Action.UPDATE
        duration = (
            timecode.format_duration(row.tc_out - row.tc_in, rate)
            if row.tc_in is not None and row.tc_out is not None
            else ""
        )
        conform = change.conform
        rows.append(
            {
                "index": index,
                "change": change,
                "included": index in included,
                "title": change.new_title
                if change.new_title is not None
                else (change.old_title if update else row.title),
                "old_title": change.old_title
                if change.new_title is not None
                else "",
                "tc_in": _tc(row.tc_in, rate),
                "tc_out": _tc(row.tc_out, rate),
                "old_in": _tc(change.old_in, rate)
                if update and change.old_in != row.tc_in
                else "",
                "old_out": _tc(change.old_out, rate)
                if update and change.old_out != row.tc_out
                else "",
                "duration": duration,
                "reel": pictures.get(
                    change.picture_id or -1, project.picture_title("N/A")
                )
                if change.action == merge.Action.NEW
                else "",
                "moves_to": pictures.get(change.picture_id or -1, "")
                if update
                else "",
                "offset": timecode.format_offset(conform.start_offset, rate)
                if conform
                and conform.needs_conform
                and conform.start_offset is not None
                else "",
                "difference": timecode.format_offset(
                    conform.duration_difference, rate
                )
                if conform
                and conform.needs_conform
                and conform.duration_difference is not None
                else "",
            }
        )
    return rows


def _default_included(preview: merge.Preview) -> set[int]:
    return {
        index
        for index, change in enumerate(preview.changes)
        if change.action != merge.Action.SKIP
    }


def _posted_ids(request: HttpRequest, name: str) -> set[int]:
    return {int(v) for v in request.POST.getlist(name) if v.isdigit()}


def _render_preview(
    request: HttpRequest,
    form: transfer_forms.ImportForm,
    planned: _Planned | None = None,
    *,
    error: str = "",
    notice: str = "",
    keep_choices: bool = False,
) -> HttpResponse:
    """The preview step, or the error that stopped it."""
    context: dict[str, Any] = {
        "project": form.project,
        "form": form,
        "error": error,
        "notice": notice,
    }
    if planned is not None:
        preview = planned.preview
        same_rows = request.POST.get("numbers_key") == _numbers_key(preview)
        keep = keep_choices or ("previewed" in request.POST and same_rows)
        included = (
            _posted_ids(request, "include")
            if keep
            else _default_included(preview)
        )
        missing_ids = (
            _posted_ids(request, "missing_ids")
            if keep
            else {m.cue_id for m in preview.missing}
        )
        context.update(
            {
                "planned": planned,
                "preview": preview,
                "rows": _rows(form.project, preview, included),
                "include_count": len(included),
                "missing": [
                    (m, m.cue_id in missing_ids) for m in preview.missing
                ],
                "missing_choices": form.fields["missing"].choices,  # type: ignore[attr-defined]
                "missing_choice": form.cleaned_data.get("missing")
                or apply.MissingAction.LEAVE,
                "columns": [
                    (c, f"column-{c.index}") for c in planned.parsed.columns
                ],
                "field_labels": FIELD_LABELS,
                "fields": [(f, FIELD_LABELS[f]) for f in tabular.FIELDS],
                "fingerprint": imports.fingerprint(preview),
                "numbers_key": _numbers_key(preview),
                "counts": {
                    "new": preview.count(merge.Action.NEW),
                    "update": preview.count(merge.Action.UPDATE),
                    "skip": preview.count(merge.Action.SKIP),
                },
            }
        )
    return render(request, "cues/dialogs/import_preview.html", context)


@role_required(Role.EDITOR)
@require_GET
def import_dialog(request: HttpRequest, project_id: int) -> HttpResponse:
    """The import dialog's first step."""
    project = get_object_or_404(models.Project, pk=project_id)
    return render(
        request,
        "cues/dialogs/import.html",
        {
            "project": project,
            "form": transfer_forms.ImportForm(project=project),
        },
    )


@role_required(Role.EDITOR)
@require_http_methods(["GET", "POST"])
@_limit
def import_preview(request: HttpRequest, project_id: int) -> HttpResponse:
    """Previews the posted file; GET clears the preview (Back)."""
    project = get_object_or_404(models.Project, pk=project_id)
    if request.method == "GET":
        return HttpResponse("")
    form = transfer_forms.ImportForm(
        request.POST, request.FILES, project=project
    )
    if not form.is_valid():
        return _render_preview(request, form)
    try:
        planned = _plan(form)
    except (base.ImportFormatError, ValueError) as exc:
        return _render_preview(request, form, error=str(exc))
    return _render_preview(request, form, planned)


@role_required(Role.EDITOR)
@require_POST
@_limit
def import_apply(request: HttpRequest, project_id: int) -> HttpResponse:
    """Imports the previewed changes the user left ticked."""
    project = get_object_or_404(models.Project, pk=project_id)
    form = transfer_forms.ImportForm(
        request.POST, request.FILES, project=project
    )
    if not form.is_valid():
        return _render_preview(request, form)
    try:
        planned = _plan(form)
    except (base.ImportFormatError, ValueError) as exc:
        return _render_preview(request, form, error=str(exc))
    if request.POST.get("fingerprint") != imports.fingerprint(planned.preview):
        return _render_preview(request, form, planned, notice=CHANGED_SINCE)
    filename = form.cleaned_data["file"].name or ""
    try:
        result = apply.apply(
            project,
            planned.preview,
            user=_user(request),
            filename=filename,
            include=_posted_ids(request, "include"),
            missing_action=apply.MissingAction(
                form.cleaned_data.get("missing") or apply.MissingAction.LEAVE
            ),
            missing_ids=_posted_ids(request, "missing_ids"),
        )
    except ValidationError as exc:
        return _render_preview(
            request,
            form,
            planned,
            error=" ".join(exc.messages),
            keep_choices=True,
        )
    if planned.kind in {imports.Kind.CUBASE, imports.Kind.MIDI}:
        project.daw_start = planned.source.start_frames
        project.save(update_fields=["daw_start", "updated_at"])
    parts = [f"{result.created} new", f"{result.updated} updated"]
    if result.missing_changed:
        parts.append(f"{result.missing_changed} not in the file changed")
    message = f"Imported {filename}: {', '.join(parts)}. Undo reverts it."
    response = HttpResponse(status=204)
    response["HX-Trigger"] = json.dumps(
        {
            "cues:reload": True,
            "cues:changed": True,
            "dialog:close": True,
            "cues:imported": {"message": message},
        }
    )
    return response
