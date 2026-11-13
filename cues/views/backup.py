"""Backup download and restore, for admins."""

from collections.abc import Callable
import io
from typing import Any, TypeVar

from django import forms as django_forms
from django.conf import settings
from django.contrib.auth import logout
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from accounts import audit
from accounts.authz import role_required
from accounts.models import AuditAction
from accounts.models import Role
from accounts.models import User
from cues.exporters import backup
from reeltime import maintenance

CONFIRM_WORD = "RESTORE"


def _rate(group: str, request: HttpRequest) -> str:
    del group, request
    return str(settings.RATELIMIT_BACKUPS)


def _user_key(group: str, request: HttpRequest) -> str:
    del group
    return str(request.user.pk)


ViewT = TypeVar("ViewT", bound=Callable[..., Any])

_limit: Callable[[ViewT], ViewT] = ratelimit(
    group="backups", key=_user_key, rate=_rate, block=True
)


class RestoreForm(django_forms.Form):
    """The restore upload, with a typed confirmation."""

    file = django_forms.FileField(label="Backup file (.zip)")
    confirm = django_forms.CharField(
        label=f"Type {CONFIRM_WORD} to confirm",
        # The pattern lets the Restore button turn red only once the word
        # is typed (app.css); the server still checks it.
        widget=django_forms.TextInput(
            attrs={"autocomplete": "off", "pattern": CONFIRM_WORD}
        ),
    )

    def clean_file(self) -> Any:
        """Refuses files over the limit before reading them."""
        upload = self.cleaned_data["file"]
        if upload.size > backup.MAX_UPLOAD:
            raise django_forms.ValidationError("Choose a backup under 200 MB.")
        return upload

    def clean_confirm(self) -> str:
        """Must be the confirmation word, exactly."""
        text: str = self.cleaned_data["confirm"].strip()
        if text != CONFIRM_WORD:
            raise django_forms.ValidationError(f"Type {CONFIRM_WORD} exactly.")
        return text


def _user(request: HttpRequest) -> User | None:
    return request.user if isinstance(request.user, User) else None


def _page(
    request: HttpRequest, form: RestoreForm, error: str = ""
) -> HttpResponse:
    return render(
        request,
        "cues/backup.html",
        {
            "nav": "backup",
            "form": form,
            "error": error,
            "schema": backup.schema(),
        },
    )


@role_required(Role.ADMIN)
@require_http_methods(["GET", "POST"])
@_limit
def backup_page(request: HttpRequest) -> HttpResponse:
    """Download a backup, or restore one into a fresh install."""
    if request.method == "GET":
        return _page(request, RestoreForm())
    form = RestoreForm(request.POST, request.FILES)
    if not form.is_valid():
        return _page(request, form)
    upload = form.cleaned_data["file"]
    upload.seek(0)
    try:
        # The form already refused files over MAX_UPLOAD.
        checked = backup.read(upload)
        snapshot = maintenance.snapshot_before("restore", timezone.now())
        counts = backup.restore(checked)
    except backup.BackupError as exc:
        return _page(request, form, str(exc))
    actor = str(request.user)
    # The users table was replaced: sign out, then log under the old name.
    logout(request)
    audit.record(
        AuditAction.RESTORE,
        request=request,
        actor=actor,
        created_at=checked.manifest.get("created_at", ""),
        rows=sum(counts.values()),
        snapshot=str(snapshot or ""),
    )
    return render(
        request,
        "cues/backup_restored.html",
        {"counts": sorted(counts.items()), "snapshot": snapshot},
    )


@role_required(Role.ADMIN)
@require_GET
@_limit
def backup_download(request: HttpRequest) -> HttpResponse:
    """A ZIP of every table plus thumbnails."""
    now = timezone.now()
    out = io.BytesIO()
    counts = backup.write(out, now)
    audit.record(
        AuditAction.BACKUP_DOWNLOAD,
        request=request,
        user=_user(request),
        rows=sum(counts.values()),
    )
    response = HttpResponse(out.getvalue(), content_type="application/zip")
    name = f"reeltime-backup-{timezone.localtime(now):%Y%m%d-%H%M}.zip"
    response["Content-Disposition"] = content_disposition_header(
        as_attachment=True, filename=name
    )
    return response
