"""Review links: a cue's bounces and comments for someone without an account.

A cue has at most one link. Its page plays each version whose File Link
is a Dropbox link to a file (and under it the version's Audio Link, the
music alone, when it has one), and takes comments on that version: on the
whole of it, or at a frame of the video. Notes take replies and can be
resolved. A comment, a reply, resolving and removal are each one
undoable change in the cue's project; making, changing and revoking the
link aren't (see cues.review.ReviewLink).

Used by the review page (cues.views.review) and the cue page's Review
section.
"""

from collections.abc import Iterable
import dataclasses
import hashlib
import re
import secrets
import unicodedata
import urllib.parse

from django.contrib.auth import hashers
from django.db.models import Count
from django.db.models import F
from django.db.models import Max
from django.db.models import Q
from django.db.models import Sum
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

# Unicode categories left out of names: control, format, unassigned,
# private use, surrogates, and the separators that aren't a plain space.
_HIDDEN = frozenset({"Cc", "Cf", "Cn", "Co", "Cs", "Zl", "Zp"})
_DROPBOX = frozenset({"dropbox.com", "www.dropbox.com"})
_DROPBOX_FILES = "dl.dropboxusercontent.com"
# A share link to one file: /scl/fi/<id>/<name> or the older
# /s/<id>/<name>. Folders, the owner's own pages (/home, /preview) and
# transfers have nothing a guest's browser can play.
_SHARED_FILE = re.compile(r"^/(?:scl/fi|s)/[^/]+/[^/]+$")


class ReviewError(ValueError):
    """The comment or setting can't be saved; the message says why."""


def play_url(file_link: str) -> str:
    """The address a browser plays a File Link or Audio Link from, or "".

    Only Dropbox share links to a single file can be played: the link is
    rewritten to ask for the file itself (`raw=1`) rather than Dropbox's
    page about it.
    """
    try:
        parts = urllib.parse.urlsplit(file_link.strip())
        host = (parts.hostname or "").lower()
        plain = parts.netloc.lower() == host  # no port, no user:password@
    except ValueError:
        return ""
    if (
        parts.scheme != "https"
        or not plain
        or not _SHARED_FILE.match(parts.path)
    ):
        return ""
    if host == _DROPBOX_FILES:
        return urllib.parse.urlunsplit(
            ("https", host, parts.path, parts.query, "")
        )
    if host not in _DROPBOX:
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
        audio: Where its audio alone loads from, or "" for none.
    """

    version: models.CueVersion
    url: str
    current: bool
    audio: str = ""


def playable(cue: models.Cue) -> list[Playable]:
    """The cue's versions with a playable File Link, newest first."""
    return [
        Playable(
            version,
            url,
            version.pk == cue.current_version_id,
            play_url(version.audio_link),
        )
        for version in cue.versions.order_by("-created_at", "-id")
        if (url := play_url(version.file_link))
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
    """A note and its replies, as a page lists them.

    Attributes:
        comment: The note.
        timecode: Where in the picture it's about, or "" for the whole
            version.
        replies: Its replies, oldest first.
    """

    comment: models.ReviewComment
    timecode: str
    replies: list[models.ReviewComment]

    @property
    def resolved(self) -> bool:
        """Whether the note has been dealt with."""
        return self.comment.resolved_at is not None


def comments(
    link: models.ReviewLink | None, version: models.CueVersion
) -> list[Comment]:
    """A version's notes: on the whole of it first, then by frame."""
    rate = version.cue.project.rate
    base = first_frame(link, version)
    last = timecode.max_frames(rate)
    notes: list[models.ReviewComment] = []
    replies: dict[int, list[models.ReviewComment]] = {}
    for comment in version.review_comments.all():
        if comment.parent_id is None:
            notes.append(comment)
        else:
            replies.setdefault(comment.parent_id, []).append(comment)
    notes.sort(
        key=lambda c: (c.frame is not None, c.frame or 0, c.created_at, c.pk)
    )
    return [
        Comment(
            note,
            ""
            if note.frame is None
            else timecode.format_timecode(min(base + note.frame, last), rate),
            replies.get(note.pk, []),
        )
        for note in notes
    ]


def stamp(link: models.ReviewLink, version: models.CueVersion) -> str:
    """Changes whenever what a version's comment list shows would.

    The review page sends the one it has with each refresh, and gets
    nothing back while it still matches.
    """
    found = version.review_comments.aggregate(
        ids=Sum("pk"),
        latest=Max("updated_at"),
        resolved=Count("pk", filter=Q(resolved_at__isnull=False)),
    )
    latest = found["latest"].isoformat() if found["latest"] else ""
    text = (
        f"{found['ids']}|{latest}|{found['resolved']}|"
        f"{link.comments_open}|{link.preroll}"
    )
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def note_counts(version: models.CueVersion) -> tuple[int, int]:
    """A version's notes: how many, and how many aren't resolved."""
    notes = version.review_comments.filter(parent=None)
    return notes.count(), notes.filter(resolved_at=None).count()


def open_notes(cue_ids: Iterable[int]) -> dict[int, int]:
    """How many unresolved notes each cue's current version has."""
    found: dict[int, int] = {}
    rows = models.ReviewComment.objects.filter(
        parent=None,
        resolved_at=None,
        version__cue__in=list(cue_ids),
        version=F("version__cue__current_version"),
    ).values_list("version__cue_id", flat=True)
    for cue_id in rows:
        found[cue_id] = found.get(cue_id, 0) + 1
    return found


def _clean(text: str, limit: int, what: str) -> str:
    text = text.replace("\x00", "").strip()
    if not text:
        raise ReviewError(f"Enter {what}.")
    if len(text) > limit:
        raise ReviewError(f"Keep {what} to {limit:,} characters.")
    return text


def _visible(text: str) -> str:
    """Text without characters that don't show or that reorder others.

    Control and format characters (zero-width spaces, direction
    overrides) would let a name look blank or read backwards.
    """
    return "".join(
        char for char in text if unicodedata.category(char) not in _HIDDEN
    )


def _name(name: str, user: User | None) -> str:
    """The name a comment shows: the user's own, else the guest's."""
    if user is not None:
        return activity.user_name(user)[:MAX_NAME]
    return _clean(" ".join(_visible(name).split()), MAX_NAME, "your name")


def guest_name(name: str) -> str:
    """A guest's name as it's kept: trimmed, and with something to show.

    Raises:
        ReviewError: It's empty or too long.
    """
    return _name(name, None)


def add_comment(
    version: models.CueVersion,
    *,
    name: str,
    body: str,
    frame: int | None,
    user: User | None,
    parent: models.ReviewComment | None = None,
) -> models.ReviewComment:
    """Adds a note to a version, or a reply to one, as an undoable change.

    Args:
        version: The version it's about.
        name: The guest's name; a signed-in user's own name is used
            instead.
        body: The comment, as plain text.
        frame: Frames from the video's first frame, or None for the
            whole version. A reply has none.
        user: The signed-in user writing it, or None for a guest.
        parent: The note this replies to, on the same version.

    Raises:
        ReviewError: A name or comment is missing or too long, the frame
            is out of range, the note replied to isn't one of this
            version's, or the version has too many comments.
    """
    cue = version.cue
    shown = _name(name, user)
    text = _clean(body, MAX_BODY, "a comment")
    if parent is not None:
        if parent.version_id != version.pk or parent.parent_id is not None:
            raise ReviewError("That comment can't be replied to.")
        frame = None
    if frame is not None and not 0 <= frame <= timecode.max_frames(
        cue.project.rate
    ):
        raise ReviewError("That time isn't in the video.")
    what = f"Reply to {parent.name}" if parent else "Comment"
    who = shown if user else f"{shown} (guest)"
    label = f"{what} on {cue.number} {version.label} by {who}"
    with history.recording(cue.project, label, user):
        # Checked inside the transaction, which holds the write lock: the
        # note can't be deleted, nor the limit passed, in between.
        if (
            parent is not None
            and not version.review_comments.filter(pk=parent.pk).exists()
        ):
            raise ReviewError("That comment isn't there any more.")
        if version.review_comments.count() >= MAX_COMMENTS:
            raise ReviewError(
                f"{cue.number} {version.label} has {MAX_COMMENTS} comments, "
                "the most a version takes."
            )
        return models.ReviewComment.objects.create(
            version=version,
            frame=frame,
            member=user is not None,
            name=shown,
            body=text,
            parent=parent,
        )


def resolve(
    comment: models.ReviewComment, user: User | None, *, resolved: bool = True
) -> None:
    """Marks a note dealt with, or open again, as one undoable change.

    Raises:
        ReviewError: It's a reply; only notes are resolved.
    """
    if comment.parent_id is not None:
        raise ReviewError("Resolve the comment this replies to.")
    if (comment.resolved_at is not None) == resolved:
        return
    version = comment.version
    cue = version.cue
    action = "Resolve" if resolved else "Reopen"
    label = (
        f"{action} comment on {cue.number} {version.label} by {comment.name}"
    )
    with history.recording(cue.project, label, user):
        comment.resolved_at = timezone.now() if resolved else None
        comment.resolved_by = (
            activity.user_name(user)[:MAX_NAME] if resolved else ""
        )
        comment.save()


def delete_comment(comment: models.ReviewComment, user: User | None) -> None:
    """Deletes a comment and any replies to it, as one undoable change."""
    version = comment.version
    cue = version.cue
    what = "reply" if comment.parent_id else "comment"
    label = f"Delete {what} on {cue.number} {version.label} by {comment.name}"
    with history.recording(cue.project, label, user):
        # Replies first, so undo puts the note back before them.
        for reply in comment.replies.all():
            reply.delete()
        comment.delete()
