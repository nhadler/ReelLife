"""Review links: a cue's bounces and comments for someone without an account.

A cue has at most one link. Its page plays each version whose File Link
is a Dropbox link to a file, and takes comments on that version: on the
whole of it, or at a frame of the video. A comment or its removal is one
undoable change in the cue's project; making, changing and revoking the
link aren't (see cues.review.ReviewLink).

Used by the review page (cues.views.review) and the cue page's Review
section.
"""

from collections.abc import Iterable
import dataclasses
import secrets
import urllib.parse

from django.contrib.auth import hashers
from django.utils import crypto
from django.utils import timezone

from accounts.models import User
from cues import models
from cues import timecode
from cues.services import activity
from cues.services import history

MAX_NAME = 60
MAX_BODY = 2000
# A version's comments stop here, so a link can't fill the database.
MAX_COMMENTS = 500
MIN_PASSWORD = 6
# Where a review page may load video from (its media-src): Dropbox's
# site, which redirects to its file servers.
MEDIA_SOURCES = ("https://www.dropbox.com", "https://*.dropboxusercontent.com")

_DROPBOX = frozenset({"dropbox.com", "www.dropbox.com"})
_DROPBOX_FILES = "dl.dropboxusercontent.com"
# Shared folders, which have no single file to play.
_FOLDERS = ("/scl/fo/", "/sh/")


class ReviewError(ValueError):
    """The comment or setting can't be saved; the message says why."""


def video_url(file_link: str) -> str:
    """The address a browser plays a version's File Link from, or "".

    Only Dropbox links to a single file can be played: the link is
    rewritten to ask for the file itself (`raw=1`) rather than Dropbox's
    page about it.
    """
    try:
        parts = urllib.parse.urlsplit(file_link.strip())
        host = (parts.hostname or "").lower()
        plain = parts.netloc.lower() == host  # no port, no user:password@
    except ValueError:
        return ""
    if parts.scheme != "https" or not plain or len(parts.path) < 2:
        return ""
    if host == _DROPBOX_FILES:
        return urllib.parse.urlunsplit(
            ("https", host, parts.path, parts.query, "")
        )
    if host not in _DROPBOX or parts.path.startswith(_FOLDERS):
        return ""
    query = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(
            parts.query, keep_blank_values=True
        )
        if key not in ("dl", "raw")
    ]
    return urllib.parse.urlunsplit(
        (
            "https",
            "www.dropbox.com",
            parts.path,
            urllib.parse.urlencode([*query, ("raw", "1")]),
            "",
        )
    )


def create(cue: models.Cue, user: User | None) -> models.ReviewLink:
    """Makes the cue's review link, or returns the one it has."""
    link, _ = models.ReviewLink.objects.get_or_create(
        cue=cue,
        defaults={"token": secrets.token_urlsafe(32), "created_by": user},
    )
    return link


def revoke(link: models.ReviewLink) -> None:
    """Stops the link working for good. Its comments stay on the cue."""
    link.delete()


def find(token: str) -> models.ReviewLink | None:
    """The link a token opens, unless it has run out."""
    link = (
        models.ReviewLink.objects.select_related("cue__project")
        .filter(token=token)
        .first()
    )
    if link is None or expired(link):
        return None
    return link


def expired(link: models.ReviewLink) -> bool:
    """Whether the link's last day has passed."""
    return (
        link.expires_on is not None and timezone.localdate() > link.expires_on
    )


def set_password(link: models.ReviewLink, password: str) -> None:
    """Sets the link's password, or with "" removes it. Doesn't save.

    Raises:
        ReviewError: The password is too short.
    """
    if password and len(password) < MIN_PASSWORD:
        raise ReviewError(
            f"Use at least {MIN_PASSWORD} characters for the password."
        )
    link.password = hashers.make_password(password) if password else ""


def check_password(link: models.ReviewLink, password: str) -> bool:
    """Whether `password` opens a link that has one."""
    return bool(link.password) and hashers.check_password(
        password, link.password
    )


def unlock_key(link: models.ReviewLink) -> str:
    """What a browser's session keeps once it has given the password.

    It follows from the stored hash, so changing the password locks the
    link again for everyone.
    """
    return crypto.salted_hmac(
        "cues.review.unlock", link.password, algorithm="sha256"
    ).hexdigest()


def unlocked(link: models.ReviewLink, key: object) -> bool:
    """Whether a session's kept key still opens the link."""
    return isinstance(key, str) and crypto.constant_time_compare(
        key, unlock_key(link)
    )


@dataclasses.dataclass(frozen=True)
class Playable:
    """A version the review page can play.

    Attributes:
        version: The version.
        url: Where the browser loads its video from.
        current: Whether it's the cue's current version.
    """

    version: models.CueVersion
    url: str
    current: bool


def playable(cue: models.Cue) -> list[Playable]:
    """The cue's versions with a playable File Link, newest first."""
    return [
        Playable(version, url, version.pk == cue.current_version_id)
        for version in cue.versions.order_by("-created_at", "-id")
        if (url := video_url(version.file_link))
    ]


def choose(found: list[Playable], label: str) -> Playable | None:
    """The version to show: the one asked for, else current, else newest."""
    if label:
        return next((p for p in found if p.version.label == label), None)
    return next((p for p in found if p.current), found[0] if found else None)


def first_frame(
    link: models.ReviewLink | None, version: models.CueVersion
) -> int:
    """The timecode of a version's video's first frame, in frames.

    The version's IN (else the cue's) less the link's pre-roll.
    """
    start = version.tc_in if version.tc_in is not None else version.cue.tc_in
    return max(0, (start or 0) - (link.preroll if link else 0))


@dataclasses.dataclass(frozen=True)
class Comment:
    """A comment as a page lists it.

    Attributes:
        comment: The comment.
        timecode: Where in the picture it's about, or "" for the whole
            version.
    """

    comment: models.ReviewComment
    timecode: str


def comments(
    link: models.ReviewLink | None, version: models.CueVersion
) -> list[Comment]:
    """A version's comments: on the whole of it first, then by frame."""
    rate = version.cue.project.rate
    base = first_frame(link, version)
    last = timecode.max_frames(rate)
    listed = sorted(
        version.review_comments.all(),
        key=lambda c: (c.frame is not None, c.frame or 0, c.created_at, c.pk),
    )
    return [
        Comment(
            comment,
            ""
            if comment.frame is None
            else timecode.format_timecode(
                min(base + comment.frame, last), rate
            ),
        )
        for comment in listed
    ]


def counts(versions: Iterable[models.CueVersion]) -> dict[int, int]:
    """How many comments each version has."""
    found: dict[int, int] = {}
    for version_id in models.ReviewComment.objects.filter(
        version__in=list(versions)
    ).values_list("version_id", flat=True):
        found[version_id] = found.get(version_id, 0) + 1
    return found


def _clean(text: str, limit: int, what: str) -> str:
    text = text.replace("\x00", "").strip()
    if not text:
        raise ReviewError(f"Enter {what}.")
    if len(text) > limit:
        raise ReviewError(f"Keep {what} to {limit:,} characters.")
    return text


def add_comment(
    version: models.CueVersion,
    *,
    name: str,
    body: str,
    frame: int | None,
    user: User | None,
) -> models.ReviewComment:
    """Adds a comment to a version, as one undoable change.

    Args:
        version: The version it's about.
        name: The guest's name; a signed-in user's own name is used
            instead.
        body: The comment, as plain text.
        frame: Frames from the video's first frame, or None for the
            whole version.
        user: The signed-in user writing it, or None for a guest.

    Raises:
        ReviewError: A name or comment is missing or too long, the frame
            is out of range, or the version has too many comments.
    """
    cue = version.cue
    shown = (
        activity.user_name(user)[:MAX_NAME]
        if user
        else _clean(" ".join(name.split()), MAX_NAME, "your name")
    )
    text = _clean(body, MAX_BODY, "a comment")
    if frame is not None and not 0 <= frame <= timecode.max_frames(
        cue.project.rate
    ):
        raise ReviewError("That time isn't in the video.")
    if version.review_comments.count() >= MAX_COMMENTS:
        raise ReviewError(
            f"{cue.number} {version.label} has {MAX_COMMENTS} comments, "
            "the most a version takes."
        )
    label = f"Comment on {cue.number} {version.label} by {shown}"
    with history.recording(cue.project, label, user):
        return models.ReviewComment.objects.create(
            version=version,
            frame=frame,
            member=user is not None,
            name=shown,
            body=text,
        )


def delete_comment(comment: models.ReviewComment, user: User | None) -> None:
    """Deletes a comment, as one undoable change."""
    version = comment.version
    cue = version.cue
    label = f"Delete comment on {cue.number} {version.label} by {comment.name}"
    with history.recording(cue.project, label, user):
        comment.delete()
