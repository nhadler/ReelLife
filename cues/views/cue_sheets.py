"""The cue sheet export: dialog, checklist and download.

Like the cue log, it only reads, so viewers may use it. The checklist
redraws as the choices change; the sheet downloads whatever it says, so
a draft can go to someone to fill gaps.
"""

from django.http import HttpRequest
from django.http import HttpResponse
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_GET

from cues import models
from cues import transfer_forms
from cues.exporters.cue_sheet import generic
from cues.exporters.cue_sheet import model
from cues.exporters.cue_sheet import rapidcue
from cues.views.exports import _limit

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _sheet(
    project: models.Project, form: transfer_forms.CueSheetForm
) -> model.CueSheet:
    return model.build(project, form.options(), timezone.localdate())


@require_GET
def dialog(request: HttpRequest, project_id: int) -> HttpResponse:
    """The export dialog, with the checklist for the default choices."""
    project = get_object_or_404(models.Project, pk=project_id)
    # The checklist starts from the form's defaults.
    defaults = transfer_forms.CueSheetForm(
        {"format": "rapidcue", "include_ipi": "on"}
    )
    defaults.is_valid()
    sheet = _sheet(project, defaults)
    return render(
        request,
        "cues/dialogs/cue_sheet.html",
        {
            "project": project,
            "form": transfer_forms.CueSheetForm(),
            "sheet": sheet,
            "issues": model.checklist(sheet),
            # 23.976 and the like: timecode seconds aren't clock seconds.
            "fractional": project.rate.fps.denominator != 1,
        },
    )


@require_GET
def checklist(request: HttpRequest, project_id: int) -> HttpResponse:
    """The checklist for the chosen options, redrawn in the dialog."""
    project = get_object_or_404(models.Project, pk=project_id)
    form = transfer_forms.CueSheetForm(request.GET)
    if not form.is_valid():
        return HttpResponseBadRequest("Those choices aren't valid.")
    sheet = _sheet(project, form)
    return render(
        request,
        "cues/dialogs/cue_sheet_checklist.html",
        {"project": project, "sheet": sheet, "issues": model.checklist(sheet)},
    )


@require_GET
@_limit
def download(request: HttpRequest, project_id: int) -> HttpResponse:
    """Builds and sends the cue sheet."""
    project = get_object_or_404(models.Project, pk=project_id)
    form = transfer_forms.CueSheetForm(request.GET)
    if not form.is_valid():
        return HttpResponseBadRequest("Those choices aren't valid.")
    sheet = _sheet(project, form)
    today = timezone.localdate()
    name = rapidcue.filename(sheet, today)
    if form.cleaned_data["format"] == "csv":
        body, kind = generic.to_csv(sheet), "text/csv; charset=utf-8"
        name = name.removesuffix(".xlsx") + ".csv"
    else:
        body, kind = rapidcue.to_xlsx(sheet), _XLSX
    response = HttpResponse(body, content_type=kind)
    response["Content-Disposition"] = content_disposition_header(
        as_attachment=True, filename=name
    )
    return response
