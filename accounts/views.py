"""Sign-in, sign-out and the pages around them."""

from collections.abc import Callable
from typing import Any, TypeVar

from django.conf import settings
from django.contrib import auth
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import HttpResponseBase
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST
from django.views.generic import View
from django_ratelimit.decorators import ratelimit
from mozilla_django_oidc import views as oidc_views

from accounts import audit
from accounts import forms
from accounts import models
from accounts import oidc
from reellife import net

ViewT = TypeVar("ViewT", bound=Callable[..., Any])


def _login_rate(group: str, request: HttpRequest) -> str:
    del group, request
    return str(settings.RATELIMIT_LOGIN)


# Applied to every step of signing in; a 429 page is shown when exceeded.
_limit_login = method_decorator(
    ratelimit(
        group="login",
        key=net.ratelimit_key,
        rate=_login_rate,
        method=ratelimit.ALL,
        block=True,
    ),
    name="dispatch",
)


def _safe_next(request: HttpRequest) -> str:
    target = request.GET.get("next", "")
    if url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target
    return ""


@require_GET
def login_page(request: HttpRequest) -> HttpResponse:
    """Shows the single 'Sign in with PocketID' button."""
    if request.user.is_authenticated:
        return HttpResponseRedirect(_safe_next(request) or "/")
    return render(
        request,
        "accounts/login.html",
        {
            "next": _safe_next(request),
            "breakglass_enabled": settings.BREAKGLASS_ENABLED,
        },
    )


@_limit_login
class LoginStartView(oidc_views.OIDCAuthenticationRequestView):  # type: ignore[misc]
    """Redirects to the provider's authorization endpoint."""

    # The base __init__ reads the endpoint from settings; ours comes from
    # discovery, fetched in get() so a provider outage shows a clear page.
    def __init__(self, **kwargs: Any) -> None:  # pylint: disable=super-init-not-called
        """Sets the client ID; the endpoint is resolved per request."""
        View.__init__(self, **kwargs)  # pylint: disable=non-parent-init-called
        self.OIDC_RP_CLIENT_ID = settings.OIDC_RP_CLIENT_ID  # pylint: disable=invalid-name
        self.OIDC_OP_AUTH_ENDPOINT = ""  # pylint: disable=invalid-name

    def get(self, request: HttpRequest) -> HttpResponseBase:
        """Starts the authorization code flow with PKCE and a nonce."""
        try:
            config = oidc.provider_config()
        except oidc.DiscoveryError:
            return render(
                request, "accounts/provider_unavailable.html", status=503
            )
        self.OIDC_OP_AUTH_ENDPOINT = config.authorization_endpoint
        response: HttpResponseBase = super().get(request)
        return response

    def get_extra_params(self, request: HttpRequest) -> dict[str, str]:
        """Uses REELLIFE_BASE_URL for redirect_uri, not the Host header."""
        del request
        return {"redirect_uri": oidc.callback_url()}


@_limit_login
class CallbackView(oidc_views.OIDCAuthenticationCallbackView):  # type: ignore[misc]
    """Handles the provider's redirect back after sign-in."""

    def get(self, request: HttpRequest) -> HttpResponseBase:
        """Completes sign-in, or reports the provider's error.

        The base class signs the current user out on any `?error=`
        callback, without checking `state`, so a link or image pointing
        at the callback could sign anyone out. Here an error only counts
        when its state is one this session started, and that state is
        then used up; any other error callback leaves the session alone.
        """
        if not request.GET.get("error"):
            response: HttpResponseBase = super().get(request)
            return response
        state = request.GET.get("state", "")
        pending = request.session.get("oidc_states")
        if isinstance(pending, dict) and state in pending:
            del pending[state]
            request.session.modified = True
            if request.user.is_authenticated:
                auth.logout(request)
        return self.login_failure()

    def login_failure(self) -> HttpResponse:
        """Shows why sign-in didn't complete, without internal detail."""
        reason = getattr(self.request, "reellife_login_refused", None)
        if reason:
            return render(
                self.request,
                "accounts/refused.html",
                {"reason": reason},
                status=403,
            )
        return render(self.request, "accounts/login_failed.html", status=400)


def _breakglass_rate(group: str, request: HttpRequest) -> str:
    del group, request
    return str(settings.RATELIMIT_BREAKGLASS)


# Strict: break-glass is a password form, so brute force is the risk.
_limit_breakglass: Callable[[ViewT], ViewT] = ratelimit(
    group="breakglass",
    key=net.ratelimit_key,
    rate=_breakglass_rate,
    method="POST",
    block=True,
)


@_limit_breakglass
def local_login(request: HttpRequest) -> HttpResponse:
    """Break-glass sign-in: password plus TOTP, audit-logged every time.

    Raises:
        Http404: When break-glass is disabled, so the page doesn't exist.
    """
    if not settings.BREAKGLASS_ENABLED:
        raise Http404
    form = forms.BreakglassForm(request.POST or None)
    failed = False
    if request.method == "POST":
        user = None
        if form.is_valid():
            user = auth.authenticate(
                request,
                username=form.cleaned_data["username"],
                password=form.cleaned_data["password"],
                otp_token=form.cleaned_data["otp_token"],
            )
        if isinstance(user, models.User):
            auth.login(request, user, backend="accounts.breakglass.Backend")
            audit.record(
                models.AuditAction.BREAKGLASS_LOGIN, request=request, user=user
            )
            return HttpResponseRedirect("/")
        audit.record(
            models.AuditAction.BREAKGLASS_FAILED,
            request=request,
            actor=request.POST.get("username", "")[:150],
        )
        failed = True
    return render(
        request,
        "accounts/local_login.html",
        {"form": form, "failed": failed},
        status=400 if failed else 200,
    )


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    """Signs out locally, then at the provider when it supports that."""
    target = oidc.provider_logout_url(request) or settings.LOGOUT_REDIRECT_URL
    if isinstance(request.user, models.User):
        audit.record(
            models.AuditAction.LOGOUT, request=request, user=request.user
        )
    auth.logout(request)
    return HttpResponseRedirect(target)


@require_GET
def picture(request: HttpRequest) -> HttpResponse:
    """The signed-in user's profile picture, as fetched at sign-in.

    The top bar links here with the picture's hash, so a new picture gets
    a new URL and the browser may keep this one for good.
    """
    user = request.user
    if not isinstance(user, models.User) or not user.picture:
        raise Http404("No picture")
    response = HttpResponse(bytes(user.picture), content_type="image/png")
    response["Cache-Control"] = "private, max-age=31536000, immutable"
    return response
