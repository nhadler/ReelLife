"""Deadlines, days off and the schedule's own records.

Re-exported from cues.models.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

from cues.model_base import Timestamped
from cues.model_base import _history


class Deadline(Timestamped):
    """A project deadline (composition, mix, delivery, ...)."""

    class Kind(models.TextChoices):
        COMPOSITION = "composition", "Composition"
        ORCHESTRATION = "orchestration", "Orchestration"
        MIX = "mix", "Mix"
        DELIVERY = "delivery", "Delivery"
        CUSTOM = "custom", "Custom"

    project = models.ForeignKey(
        "cues.Project", on_delete=models.CASCADE, related_name="deadlines"
    )
    kind = models.CharField(max_length=15, choices=Kind.choices)
    label = models.CharField(max_length=100, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField()

    class Meta:
        ordering = ["end_date"]
        constraints = [
            models.CheckConstraint(
                condition=Q(start_date__isnull=True)
                | Q(end_date__gte=models.F("start_date")),
                name="deadline_ends_after_start",
            ),
        ]

    def __str__(self) -> str:
        """Returns the label, or the kind when there is none."""
        return self.label or self.get_kind_display()


class DayOff(models.Model):
    """A day not worked, skipped in pace maths.

    Shared (no project): the composer's own days off, for every project.
    With a project: only that project, e.g. when its studio is closed.
    """

    project = models.ForeignKey(
        "cues.Project",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="days_off",
    )
    date = models.DateField()
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "day off"
        verbose_name_plural = "days off"
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(
                fields=["date"],
                condition=models.Q(project__isnull=True),
                name="day_off_shared_once",
            ),
            models.UniqueConstraint(
                fields=["project", "date"], name="day_off_once_per_project"
            ),
        ]

    def __str__(self) -> str:
        """Returns the date."""
        return self.date.isoformat()


class SchedulePin(Timestamped):
    """A cue the composer fixed to a day ("Schedule On…").

    The rest of the plan flows around it. Once the day has passed, an
    unfinished pinned cue just rejoins the flow. Tracked by history, so
    pinning undoes.
    """

    cue = models.OneToOneField(
        "cues.Cue", on_delete=models.CASCADE, related_name="pin"
    )
    date = models.DateField()

    history = _history()

    class Meta:
        verbose_name = "schedule pin"

    def __str__(self) -> str:
        """Returns the cue and the day."""
        return f"{self.cue} on {self.date.isoformat()}"


class WritingLog(Timestamped):
    """How much of a cue the composer wrote on a day ("Log Written…").

    The schedule counts only logged work as done: a cue's logs of its
    current kind of work (writing, or revising) come off what's left.
    One per cue and day; logging again replaces it. Tracked by history,
    so it undoes.
    """

    cue = models.ForeignKey(
        "cues.Cue", on_delete=models.CASCADE, related_name="writing_logs"
    )
    date = models.DateField()
    # Seconds of the cue's music written that day.
    seconds = models.PositiveIntegerField()
    # Revision work is logged apart from writing.
    revise = models.BooleanField(default=False)

    history = _history()

    class Meta:
        verbose_name = "writing log"
        ordering = ["date", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["cue", "date"], name="writing_log_once_a_day"
            ),
        ]

    def __str__(self) -> str:
        """Returns the cue, the day and how much."""
        return f"{self.cue} on {self.date.isoformat()}: {self.seconds} s"


class ScheduleEntry(models.Model):
    """One cue's share of a day's plan, saved on that day.

    The first look at the schedule each day (or the nightly job) saves
    that day's plan, so past days show what was planned beside what was
    written (WritingLog). A record, not undoable; pruned after a year.
    """

    date = models.DateField()
    cue = models.ForeignKey(
        "cues.Cue", on_delete=models.CASCADE, related_name="schedule_entries"
    )
    # Revision work is planned and credited apart from writing.
    revise = models.BooleanField(default=False)
    music = models.DecimalField(max_digits=10, decimal_places=3)
    effort = models.DecimalField(max_digits=10, decimal_places=3)

    class Meta:
        verbose_name = "schedule entry"
        verbose_name_plural = "schedule entries"
        ordering = ["date", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["date", "cue"], name="schedule_entry_once_a_day"
            ),
        ]

    def __str__(self) -> str:
        """Returns the day and the cue's id."""
        return f"{self.date.isoformat()} cue {self.cue_id}"


class CalendarFeed(models.Model):
    """A user's calendar subscription: the schedule as an .ics feed.

    Only the SHA-256 of its token is kept; the link is shown once, when
    it's made. Making a new one revokes the old.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="calendar_feed",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        """Returns whose feed it is."""
        return f"Calendar feed for {self.user}"
