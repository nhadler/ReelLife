"""The schedule in a calendar app: the feed link page and the feed.

The feed (`/calendar/<token>.ics`) is the one page outside sign-in: a
calendar app can't sign in, so the link's secret token stands for its
user (cues.services.feed).
"""

from collections.abc import Callable
from typing import Any, TypeVar, cast

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from accounts.authz import role_required
from accounts.models import Role
from accounts.models import User
from cues import models
from cues.services import agenda as agenda_service
from cues.services import feed as feed_service
from reeltime import net

ViewT = TypeVar("ViewT", bound=Callable[..., Any])


def _rate(group: str, request: HttpRequest) -> str:
    del group, request
    return str(settings.RATELIMIT_CALENDAR)


# Per client address: the feed is reachable without signing in.
_limit: Callable[[ViewT], ViewT] = ratelimit(
    group="calendar", key=net.ratelimit_key, rate=_rate, block=True
)


def _ics(content: bytes, *, attachment: bool) -> HttpResponse:
    response = HttpResponse(
        content, content_type="text/calendar; charset=utf-8"
    )
    if attachment:
        response["Content-Disposition"] = (
            'attachment; filename="reeltime-schedule.ics"'
        )
    return response


@role_required(Role.VIEWER)
@require_http_methods(["GET", "POST"])
def feed_page(request: HttpRequest) -> HttpResponse:
    """Make, replace or revoke your calendar link (any role)."""
    # Signed in: the middleware sends anyone else to the sign-in page.
    user = cast(User, request.user)
    link = ""
    if request.method == "POST":
        if request.POST.get("action") == "revoke":
            feed_service.revoke(user)
        else:
            secret = feed_service.create(user)
            link = f"{settings.BASE_URL}/calendar/{secret}.ics"
    return render(
        request,
        "cues/calendar_feed.html",
        {
            "nav": "",
            "feed": models.CalendarFeed.objects.filter(user=user).first(),
            "link": link,
            "webcal": link.replace("https://", "webcal://", 1).replace(
                "http://", "webcal://", 1
            ),
        },
    )


@require_GET
def download(request: HttpRequest) -> HttpResponse:
    """The schedule as a one-off .ics file, for any signed-in user."""
    del request
    agenda = agenda_service.build(timezone.localdate())
    return _ics(feed_service.ics(agenda, settings.BASE_URL), attachment=True)


@login_not_required
@require_GET
@_limit
def feed(request: HttpRequest, token: str) -> HttpResponse:
    """The subscription itself: the schedule, for whoever holds the link."""
    del request
    found = feed_service.find(token)
    if found is None:
        raise Http404("No such calendar.")
    found.last_used_at = timezone.now()
    found.save(update_fields=["last_used_at"])
    agenda = agenda_service.build(timezone.localdate())
    return _ics(feed_service.ics(agenda, settings.BASE_URL), attachment=False)
