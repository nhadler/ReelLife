"""Admin for users and the audit log."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.http import HttpRequest

from accounts import models


@admin.register(models.User)
class UserAdmin(BaseUserAdmin):  # type: ignore[type-arg]
    """Shows the OIDC subject and role alongside Django's user fields."""

    list_display = ("username", "email", "role", "oidc_sub", "last_login")
    readonly_fields = ("oidc_sub",)
    fieldsets = (
        *(BaseUserAdmin.fieldsets or ()),
        ("Identity", {"fields": ("oidc_sub", "role")}),
    )


@admin.register(models.AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Read-only view of the audit log."""

    list_display = ("created_at", "action", "actor", "ip")
    list_filter = ("action",)
    search_fields = ("actor", "ip")
    date_hierarchy = "created_at"

    def has_add_permission(self, request: HttpRequest) -> bool:
        """The log is written by the app only."""
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: models.AuditEvent | None = None
    ) -> bool:
        """Entries can't be edited."""
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: models.AuditEvent | None = None
    ) -> bool:
        """Entries are only removed by the one-year retention job."""
        return False
