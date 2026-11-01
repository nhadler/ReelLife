"""Writers, publishers and shares.

The shared directory, a cue's shares, and a project's default shares.
"""

from collections import Counter
from typing import Any

from django.contrib import messages
from django.db.models import Count
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.http import QueryDict
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.utils.text import capfirst
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from accounts import roles
from accounts.models import Role
from accounts.models import User
from cues import change_labels
from cues import models
from cues import share_forms
from cues.exporters import credits as credits_exporter
from cues.services import activity
from cues.services import history
from cues.services import shares


def _user(request: HttpRequest) -> User | None:
    return request.user if isinstance(request.user, User) else None


def _can_edit(request: HttpRequest) -> bool:
    return roles.at_least(getattr(request.user, "role", ""), Role.EDITOR)


@require_http_methods(["GET", "POST"])
def credits_page(request: HttpRequest) -> HttpResponse:
    """The writers and publishers every project draws its shares from."""
    data = request.POST if request.method == "POST" else None
    kinds = models.Party.Kind
    writers = share_forms.WriterFormSet(
        data,
        queryset=models.Party.objects.filter(kind=kinds.WRITER).order_by(
            "last_name", "first_name"
        ),
        prefix="writers",
    )
    publishers = share_forms.PublisherFormSet(
        data,
        queryset=models.Party.objects.filter(kind=kinds.PUBLISHER).order_by(
            "name"
        ),
        prefix="publishers",
    )
    if data is not None and writers.is_valid() and publishers.is_valid():
        label = change_labels.describe("Edit credits", writers, publishers)
        if label:
            # Credits belong to every project, so to none.
            with history.recording(None, label, _user(request)):
                writers.save()
                publishers.save()
        messages.success(request, "Saved writers and publishers.")
        return HttpResponseRedirect(reverse("cues:credits"))
    _note_uses([*writers.forms, *publishers.forms])
    return render(
        request,
        "cues/credits.html",
        {
            "nav": "credits",
            "writers": writers,
            "publishers": publishers,
            "societies": share_forms.SOCIETIES,
            "can_edit": _can_edit(request),
            # (title, icon, parties, whether people) for viewers.
            "readonly": [
                ("Writers", "person", list(writers.get_queryset()), True),
                (
                    "Publishers",
                    "building",
                    list(publishers.get_queryset()),
                    False,
                ),
            ],
            "recent": activity.entries(
                list(
                    models.ChangeSet.objects.filter(project=None)
                    .select_related("user")
                    .order_by("-created_at", "-id")[:RECENT]
                )
            ),
            "undoable": _last_credits_change(request) is not None,
        },
    )


# How many recent changes the Credits page lists.
RECENT = 8


def _last_credits_change(request: HttpRequest) -> models.ChangeSet | None:
    """The signed-in user's latest Credits change still to undo."""
    return (
        models.ChangeSet.objects.filter(
            project=None,
            user=_user(request),
            undone_at__isnull=True,
            undo_of__isnull=True,
        )
        .order_by("-created_at", "-id")
        .first()
    )


@require_POST
def credits_undo(request: HttpRequest) -> HttpResponse:
    """Undoes the signed-in user's latest change to the Credits list."""
    latest = _last_credits_change(request)
    if latest is None:
        messages.error(request, "Nothing to undo.")
    else:
        try:
            history.undo(latest, _user(request))
        except history.UndoError as exc:
            messages.error(request, capfirst(str(exc)))
        else:
            messages.success(request, f"Undid {latest.label}.")
    return HttpResponseRedirect(reverse("cues:credits"))


def _note_uses(forms: list[Any]) -> None:
    """Sets each saved party's form's `uses`: where its shares are."""
    cues = dict(
        models.CueShare.objects.values("party")
        .annotate(count=Count("cue", distinct=True))
        .values_list("party", "count")
    )
    defaults = Counter(
        models.DefaultShare.objects.values_list("party", flat=True)
    ) + Counter(models.SeriesShare.objects.values_list("party", flat=True))
    for form in forms:
        pk = form.instance.pk
        count, default = cues.get(pk, 0), defaults.get(pk, 0)
        parts = []
        if count:
            parts.append(f"{count} cue{'' if count == 1 else 's'}")
        if default:
            parts.append(f"{default} default{'' if default == 1 else 's'}")
        form.uses = ", ".join(parts)
        form.in_use = bool(count or default)


def shares_context(
    cue: models.Cue, formset: object = None, saved: bool = False
) -> dict[str, object]:
    """What the cue page's Credits section needs."""
    return {
        "cue": cue,
        "share_set": formset
        or share_forms.CueShareFormSet(instance=cue, prefix="shares"),
        "share_problems": shares.problems(
            shares.Line(s.party, s.role, s.share)
            for s in cue.shares.select_related("party")
        ),
        "shares_saved": saved,
        "shares_seen": shares.revision(cue),
    }


def _submitted(data: QueryDict) -> list[str]:
    """The rows a refused save tried to keep, as text to copy back."""
    try:
        total = int(data.get("shares-TOTAL_FORMS", "0"))
    except ValueError:
        total = 0
    rows = [
        {
            key: data.get(f"shares-{i}-{key}", "")
            for key in ("party", "role", "share", "DELETE")
        }
        for i in range(min(total, 1000))
    ]
    rows = [row for row in rows if row["party"].isdigit() and not row["DELETE"]]
    parties = models.Party.objects.in_bulk([int(row["party"]) for row in rows])
    return [
        f"{parties[int(row['party'])]} {row['role']} {row['share']}%"
        for row in rows
        if int(row["party"]) in parties
    ]


@require_POST
def cue_shares(request: HttpRequest, cue_id: int) -> HttpResponse:
    """Saves a cue's shares; answers with the section redrawn."""
    cue = get_object_or_404(
        models.Cue.objects.select_related("project"), pk=cue_id
    )
    if request.POST.get("shares_seen") != shares.revision(cue):
        return render(
            request,
            "cues/details/shares.html",
            {
                **shares_context(cue),
                "can_edit": True,
                "shares_conflict": _submitted(request.POST),
            },
        )
    formset = share_forms.CueShareFormSet(
        request.POST, instance=cue, prefix="shares"
    )
    saved = False
    if formset.is_valid():
        shares.set_shares(cue, formset.lines(), _user(request))
        formset = None
        saved = True
    return render(
        request,
        "cues/details/shares.html",
        {**shares_context(cue, formset, saved), "can_edit": True},
    )


@require_POST
def apply_defaults(request: HttpRequest, project_id: int) -> HttpResponse:
    """Gives the project's compositions its default shares."""
    project = get_object_or_404(models.Project, pk=project_id)
    count = shares.apply_defaults(
        project,
        replace=request.POST.get("replace") == "on",
        user=_user(request),
    )
    messages.success(
        request,
        f"Gave {count} cue{'' if count == 1 else 's'} the default shares."
        if count
        else "No cues needed the default shares.",
    )
    return HttpResponseRedirect(
        reverse("cues:settings_cue_sheet", args=[project.pk])
    )


_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@require_GET
def credits_export(request: HttpRequest, kind: str) -> HttpResponse:
    """The Credits list as CSV or Excel; viewers too, as it only reads."""
    if kind not in _TYPES:
        raise Http404("No such format.")
    body = (
        credits_exporter.to_csv()
        if kind == "csv"
        else credits_exporter.to_xlsx()
    )
    response = HttpResponse(body, content_type=_TYPES[kind])
    response["Content-Disposition"] = content_disposition_header(
        as_attachment=True,
        filename=credits_exporter.filename(timezone.localdate(), f".{kind}"),
    )
    return response
