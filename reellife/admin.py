"""The Django admin, limited to ReelLife admins."""

from django.conf import settings
from django.contrib import admin
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.http import HttpResponse
from django.utils.decorators import method_decorator

from accounts import roles
from accounts import views as account_views
from accounts.models import Role


class ReelLifeAdminSite(admin.AdminSite):
    """Admin site that uses ReelLife sign-in instead of its own."""

    site_header = "ReelLife admin"
    site_title = "ReelLife admin"

    def has_permission(self, request: HttpRequest) -> bool:
        """Admins only, by role rather than Django's is_staff flag."""
        user = request.user
        return bool(
            user.is_active
            and roles.at_least(getattr(user, "role", ""), Role.ADMIN)
        )

    @method_decorator(login_not_required)
    def login(
        self, request: HttpRequest, extra_context: object = None
    ) -> HttpResponse:
        """Sends people to the PocketID sign-in, never a password form.

        The admin's own form would accept a password without the TOTP the
        break-glass sign-in requires, so it is never shown.

        Raises:
            PermissionDenied: For signed-in users who aren't admins.
        """
        del extra_context
        if request.user.is_authenticated:
            raise PermissionDenied
        return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)

    def logout(  # type: ignore[override]
        self, request: HttpRequest, extra_context: object = None
    ) -> HttpResponse:
        """Uses ReelLife's logout, which also ends the provider session."""
        del extra_context
        return account_views.logout_view(request)
