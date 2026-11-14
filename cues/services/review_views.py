"""Who watched a review page, and how much of it.

The page starts a visit when it opens and reports back while it stays
open: how long the video or audio has played, and the furthest frame
reached. A reload within half an hour carries on the same visit. Guests
are known by the name they gave to watch; nothing else about them is
kept.

Used by the review page (cues.views.review) and the cue page's Review
section.
"""

import dataclasses
import datetime
import secrets

from django.utils import timezone

from cues import models
from cues import timecode
from cues.services import review
from cues.services.summaries import clock

# A reload this soon after the last sign of life is the same visit.
SAME_VISIT = datetime.timedelta(minutes=30)
# A version keeps its newest visits; older ones are dropped.
MAX_VIEWS = 200
# How far reported play time may run ahead of the visit's own length:
# the page's clock and the server's needn't agree to the second.
_SLACK = 30


def new_browser() -> str:
    """An id for a browser's session, to tell its visits apart."""
    return secrets.token_hex(16)


def start(
    version: models.CueVersion, *, name: str, member: bool, browser: str
) -> models.ReviewView:
    """Starts a visit, or carries on this browser's recent one."""
    now = timezone.now()
    recent = (
        version.review_views.filter(
            browser=browser, last_seen_at__gte=now - SAME_VISIT
        )
        .order_by("-last_seen_at")
        .first()
    )
    if recent is not None:
        recent.name = name
        recent.last_seen_at = now
        recent.save(update_fields=["name", "last_seen_at"])
        return recent
    view = models.ReviewView.objects.create(
        version=version,
        key=secrets.token_hex(16),
        browser=browser,
        name=name,
        member=member,
        started_at=now,
        last_seen_at=now,
    )
    keep = list(version.review_views.values_list("pk", flat=True)[:MAX_VIEWS])
    version.review_views.exclude(pk__in=keep).delete()
    return view


def report(
    cue: models.Cue, key: str, *, played: int, furthest: int | None
) -> bool:
    """Records what a visit's page says it has played so far.

    Play time and the furthest frame only go up, and play time can't
    pass the time since the visit started.

    Returns:
        Whether the key is a visit to one of this cue's versions.
    """
    view = models.ReviewView.objects.filter(key=key, version__cue=cue).first()
    if view is None:
        return False
    now = timezone.now()
    open_for = int((now - view.started_at).total_seconds()) + _SLACK
    view.played = max(view.played, min(max(played, 0), open_for))
    last = timecode.max_frames(cue.project.rate)
    if furthest is not None and 0 <= furthest <= last:
        view.furthest = max(view.furthest or 0, furthest)
    view.last_seen_at = now
    view.save(update_fields=["played", "furthest", "last_seen_at"])
    return True


@dataclasses.dataclass(frozen=True)
class Visit:
    """A visit as the cue page lists it.

    Attributes:
        view: The visit.
        played: How long the video or audio played, as m:ss.
        open_for: How long the page was open, as m:ss.
        reached: The timecode of the furthest frame reached, or "".
    """

    view: models.ReviewView
    played: str
    open_for: str
    reached: str


def visits(
    link: models.ReviewLink | None, version: models.CueVersion
) -> list[Visit]:
    """A version's visits, newest first."""
    rate = version.cue.project.rate
    base = review.first_frame(link, version)
    last = timecode.max_frames(rate)
    return [
        Visit(
            view,
            clock(view.played),
            clock(int((view.last_seen_at - view.started_at).total_seconds())),
            ""
            if view.furthest is None
            else timecode.format_timecode(
                min(base + view.furthest, last), rate
            ),
        )
        for view in version.review_views.all()
    ]
