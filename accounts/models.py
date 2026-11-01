"""Users keyed on the OIDC subject, roles from groups, and the audit log."""

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class Role(models.TextChoices):
    """What a user may do. Refreshed from groups at login."""

    NONE = "none", "No access"
    VIEWER = "viewer", "Viewer"
    EDITOR = "editor", "Editor"
    ADMIN = "admin", "Admin"


class User(AbstractUser):
    """A person who signs in.

    OIDC users are keyed on the provider's `sub` claim, never on email.
    The break-glass local admin has no `sub`.
    """

    oidc_sub = models.CharField(
        "OIDC subject",
        max_length=255,
        unique=True,
        null=True,
        blank=True,
    )
    role = models.CharField(
        max_length=10, choices=Role.choices, default=Role.NONE
    )
    # The provider's profile picture, fetched at sign-in and shrunk to a
    # small PNG (accounts/pictures.py), so pages never load it from there.
    picture = models.BinaryField(blank=True, default=b"", editable=False)
    # Changes with the picture; the top bar links to it with this, so
    # browsers may cache it for good. Blank when there is none.
    picture_hash = models.CharField(max_length=16, blank=True, editable=False)

    def __str__(self) -> str:
        """Returns the display name, falling back to the username."""
        return self.get_full_name() or self.username

    @property
    def initials(self) -> str:
        """One or two letters for the top bar when there is no picture."""
        words = f"{self.first_name} {self.last_name}".split()
        words = words or (self.username or "?").split()
        last = words[-1][0] if len(words) > 1 else ""
        return (words[0][0] + last).upper()


class AuditAction(models.TextChoices):
    """Events the audit log records."""

    LOGIN = "login", "Signed in"
    LOGIN_FAILED = "login_failed", "Sign-in failed"
    LOGIN_REFUSED = "login_refused", "Sign-in refused"
    LOGOUT = "logout", "Signed out"
    ROLE_CHANGED = "role_changed", "Role changed"
    BREAKGLASS_LOGIN = "breakglass_login", "Break-glass sign-in"
    BREAKGLASS_FAILED = "breakglass_failed", "Break-glass sign-in failed"
    IMPORT = "import", "Import"
    CUE_SHEET_EXPORT = "cue_sheet_export", "Cue sheet export"
    BACKUP_DOWNLOAD = "backup_download", "Backup download"
    RESTORE = "restore", "Restore"
    PROJECT_DELETE = "project_delete", "Project deleted"
    SETTINGS_CHANGE = "settings_change", "Settings changed"


class AuditEvent(models.Model):
    """One security-relevant event. Kept for a year.

    `actor` snapshots who did it, so the entry still reads correctly after
    the user is renamed or deleted.
    """

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    action = models.CharField(max_length=32, choices=AuditAction.choices)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    actor = models.CharField(max_length=255, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        """Returns e.g. '2026-10-30 22:00 login ada'."""
        when = self.created_at.strftime("%Y-%m-%d %H:%M")
        return f"{when} {self.action} {self.actor}".rstrip()
