"""Deadlines and days off. Re-exported from cues.models."""

from django.db import models
from django.db.models import Q

from cues.model_base import Timestamped


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
    With a project: only that one, e.g. its studio is closed (Nick, Nov 1).
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
