"""Review links and the comments left on a cue's versions.

Re-exported from cues.models.
"""

from django.conf import settings
from django.db import models

from cues.model_base import Timestamped
from cues.model_base import _history


class ReviewLink(Timestamped):
    """A cue's review link: its bounces and comments, without signing in.

    Whoever holds the link watches the cue's versions whose File Link a
    browser can play, and comments on them. The token is kept as it is,
    so the link can be copied again later: it opens nothing this row and
    the cue's versions don't already hold. A password is kept hashed.

    Not tracked by history: revoking is final, and a deleted cue's link
    doesn't come back with the cue.
    """

    cue = models.OneToOneField(
        "cues.Cue", on_delete=models.CASCADE, related_name="review_link"
    )
    token = models.CharField(max_length=64, unique=True)
    # Django's password hash, or "" for a link without a password.
    password = models.CharField(max_length=255, blank=True)
    # The last day the link works, or None for no end.
    expires_on = models.DateField(null=True, blank=True)
    comments_open = models.BooleanField(default=True)
    # Frames of video before the version's IN (a count-in, a two-pop).
    preroll = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    last_opened_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "review link"

    def __str__(self) -> str:
        """Returns the cue it reviews."""
        return f"Review link for {self.cue}"


class ReviewComment(Timestamped):
    """A comment on one version, from its review page.

    Tracked by history, so a comment deleted on its own or with its
    version or cue comes back with undo.
    """

    version = models.ForeignKey(
        "cues.CueVersion",
        on_delete=models.CASCADE,
        related_name="review_comments",
    )
    # Frames from the video's first frame, or None for a comment on the
    # version as a whole. The timecode shown follows from it (the
    # version's IN less the link's pre-roll), so fixing those moves it.
    frame = models.PositiveIntegerField(null=True, blank=True)
    # Whether a signed-in ReelTime user wrote it, rather than a guest.
    member = models.BooleanField(default=False)
    # The name shown: the guest's, or the user's when it was written.
    name = models.CharField(max_length=60)
    body = models.TextField()

    history = _history()

    class Meta:
        verbose_name = "review comment"
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        """Returns who commented on what."""
        return f"{self.name} on {self.version}"
