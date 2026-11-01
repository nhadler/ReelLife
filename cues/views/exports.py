"""The export dialog and its downloads (9).

Exports only read, so viewers may use them too. The dialog is a plain
GET form: the browser downloads the file and the page stays put.
"""

from collections.abc import Callable
import datetime
from typing import Any, TypeVar

from django.conf import settings
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_GET
from django_ratelimit.decorators import ratelimit

from cues import models
from cues import transfer_forms
from cues.api import grid
from cues.exporters import cue_log
from cues.exporters import version_notes
from cues.exporters import wall_chart

_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@require_GET
def export_dialog(request: HttpRequest, project_id: int) -> HttpResponse:
    """The export dialog, opened from the grid with its view and rows."""
    project = get_object_or_404(models.Project, pk=project_id)
    views = grid.views_for(project)
    ids = request.GET.get("ids", "")
    initial = {
        "preset": request.GET.get("view", transfer_forms.ExportForm.CUSTOM),
        "ids": ids,
        "scope": "shown" if ids else "all",
    }
    shown = len([p for p in ids.split(",") if p.strip().isdigit()])
    return render(
        request,
        "cues/dialogs/export.html",
        {
            "project": project,
            "form": transfer_forms.ExportForm(
                initial=initial, project=project, views=views
            ),
            "shown": shown,
            "total": project.cues.count(),
        },
    )


def _rate(group: str, request: HttpRequest) -> str:
    del group, request
    return str(settings.RATELIMIT_EXPORTS)


def _user_key(group: str, request: HttpRequest) -> str:
    del group
    return str(request.user.pk)


ViewT = TypeVar("ViewT", bound=Callable[..., Any])

_limit: Callable[[ViewT], ViewT] = ratelimit(
    group="exports", key=_user_key, rate=_rate, block=True
)


@require_GET
@_limit
def export_download(request: HttpRequest, project_id: int) -> HttpResponse:
    """Builds and sends the cue log, wall chart or version notes."""
    project = get_object_or_404(models.Project, pk=project_id)
    form = transfer_forms.ExportForm(
        request.GET, project=project, views=grid.views_for(project)
    )
    if not form.is_valid():
        return HttpResponseBadRequest(
            " ".join(str(error) for error in form.errors.get("leader", []))
            or "Those export choices aren't valid."
        )
    data = form.cleaned_data
    cue_ids = data["ids"] if data["scope"] == "shown" else None
    today = timezone.localdate()
    report = data["report"]
    if report == "wall_chart":
        body = wall_chart.wall_chart(project, cue_ids, today)
        return _download(body, project, today, ".pdf", "Wall Chart")
    if report == "version_notes":
        body = version_notes.to_pdf(
            project,
            all_versions=data["all_versions"],
            cue_ids=cue_ids,
            today=today,
        )
        return _download(body, project, today, ".pdf", "Version Notes")
    column_ids = form.column_ids()
    if not column_ids:
        return HttpResponseBadRequest("Choose at least one column.")
    log = cue_log.build(
        project,
        column_ids,
        cue_ids=cue_ids,
        exclude_empty=data["exclude_empty"],
        totals=data["totals"],
        show_progress=data["progress"],
        long_play_time=data["long_play_time"],
    )
    kind = data["format"]
    if kind == "csv":
        body = cue_log.to_csv(log)
    elif kind == "pdf":
        body = cue_log.to_pdf(log, today)
    else:
        body = cue_log.to_xlsx(log, today)
    return _download(body, project, today, f".{kind}", "Cue Log")


def _download(
    body: bytes,
    project: models.Project,
    today: datetime.date,
    suffix: str,
    report: str,
) -> HttpResponse:
    response = HttpResponse(body, content_type=_TYPES[suffix.lstrip(".")])
    response["Content-Disposition"] = content_disposition_header(
        as_attachment=True,
        filename=cue_log.filename(project, today, suffix, report),
    )
    return response
