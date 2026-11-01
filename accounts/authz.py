"""Role checks for every view, enforced in one place.

LoginRequiredMiddleware sends anonymous users to the login page. Then
RoleMiddleware requires, for every non-public view:

- viewer for safe methods (GET, HEAD, OPTIONS),
- editor for everything else,

unless the view declares its own minimum with @role_required. Admin-only
pages use @role_required(Role.ADMIN); the Django admin enforces admin
through its site's has_permission.
"""

from collections.abc import Callable
from typing import Any, TypeVar

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.http import HttpResponseBase

from accounts import roles
from accounts.models import Role

_ATTR = "reellife_minimum_role"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

ViewT = TypeVar("ViewT", bound=Callable[..., Any])


def role_required(minimum: Role) -> Callable[[ViewT], ViewT]:
    """Sets the minimum role for a view, for every HTTP method.

    Args:
        minimum: The lowest role allowed. Role.NONE allows any signed-in
            user, e.g. for a view a viewer may POST to.

    Returns:
        A decorator that marks the view; RoleMiddleware does the check.
    """

    def mark(view: ViewT) -> ViewT:
        setattr(view, _ATTR, minimum)
        return view

    return mark


def minimum_role(view: Callable[..., Any], method: str) -> Role:
    """Returns the role a request needs to reach a view."""
    declared = getattr(view, _ATTR, None)
    if isinstance(declared, Role):
        return declared
    return Role.VIEWER if method in SAFE_METHODS else Role.EDITOR


class RoleMiddleware:
    """Refuses signed-in users whose role is below a view's minimum."""

    def __init__(
        self, get_response: Callable[[HttpRequest], HttpResponseBase]
    ) -> None:
        """Stores the next handler in the chain."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        """Passes the request on; the check happens in process_view."""
        return self.get_response(request)

    def process_view(
        self,
        request: HttpRequest,
        view_func: Callable[..., Any],
        view_args: Any,
        view_kwargs: Any,
    ) -> None:
        """Raises PermissionDenied when the user's role is too low.

        Raises:
            PermissionDenied: The user is signed in but inactive or lacks
                the role.
        """
        del view_args, view_kwargs
        if not getattr(view_func, "login_required", True):
            return
        user = request.user
        if not user.is_authenticated:
            return  # LoginRequiredMiddleware has already redirected.
        if not user.is_active:
            # The backends don't load inactive users; this is a backstop.
            raise PermissionDenied
        needed = minimum_role(view_func, request.method or "GET")
        if not roles.at_least(getattr(user, "role", Role.NONE), needed):
            raise PermissionDenied
