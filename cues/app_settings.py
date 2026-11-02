"""App-wide preferences. Re-exported from cues.models."""

from decimal import Decimal

from django.core import validators
from django.db import models
from django.db.models import Q

from cues import defaults

# Difficulty and revise factors: above zero, at most 10.
_FACTOR = [
    validators.MinValueValidator(Decimal("0.1")),
    validators.MaxValueValidator(Decimal(10)),
]


class AppSettings(models.Model):
    """App-wide preferences: one row."""

    class DateFormat(models.TextChoices):
        US = "m/d/Y", "12/31/2026"
        EUROPE = "d/m/Y", "31/12/2026"
        ISO = "Y-m-d", "2026-12-31"

    class EnterBehaviour(models.TextChoices):
        SAVE = "save", "Save the Cell"
        LINE_BREAK = "line_break", "New Line in Notes"

    class VersionIncrement(models.TextChoices):
        MAJOR = "major", "Major (v1 → v2)"
        MINOR = "minor", "Minor (v1 → v1.1)"

    class Theme(models.TextChoices):
        DARK = "dark", "Dark"
        LIGHT = "light", "Light"

    class MusicTotals(models.TextChoices):
        COMPOSITIONS = "compositions", "Compositions Only"
        ALL = "all", "Compositions, Songs and Licensed"

    date_format = models.CharField(
        max_length=5, choices=DateFormat.choices, default=DateFormat.US
    )
    leading_zero = models.BooleanField(
        default=False, help_text="Show 01:00:10:00 rather than 1:00:10:00."
    )
    subframes = models.BooleanField(default=False)
    single_click_edit = models.BooleanField(default=True)
    enter_behaviour = models.CharField(
        max_length=10,
        choices=EnterBehaviour.choices,
        default=EnterBehaviour.LINE_BREAK,
    )
    version_increment = models.CharField(
        max_length=5,
        choices=VersionIncrement.choices,
        default=VersionIncrement.MAJOR,
    )
    theme = models.CharField(
        max_length=5, choices=Theme.choices, default=Theme.DARK
    )
    music_totals = models.CharField(
        max_length=12,
        choices=MusicTotals.choices,
        default=MusicTotals.COMPOSITIONS,
    )
    default_cue_number_pattern = models.CharField(
        max_length=200, default=defaults.DEFAULT_CUE_NUMBER_PATTERN
    )
    # The work scheduler: effort is a cue's music times its
    # difficulty's factor, times the revise factor for revisions.
    schedule_easy = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal(1), validators=_FACTOR
    )
    schedule_moderate = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal(2), validators=_FACTOR
    )
    schedule_hard = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal(3), validators=_FACTOR
    )
    schedule_revise = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal("0.5"),
        validators=_FACTOR,
    )
    schedule_unrated = models.CharField(
        "no difficulty counts as",
        max_length=8,
        choices=[(d, d) for d in ("Easy", "Moderate", "Hard")],
        default="Easy",
    )
    # Weighted seconds: None leaves the pace to the deadlines.
    schedule_daily_max = models.PositiveIntegerField(null=True, blank=True)
    schedule_min_part = models.PositiveIntegerField(default=600)

    class ProjectsAtOnce(models.TextChoices):
        PARALLEL = "parallel", "In Parallel"
        ONE = "one", "One at a Time"

    # Whether the Schedule works every project each day, each at its own
    # pace, or one at a time, earliest deadline first.
    schedule_projects = models.CharField(
        "projects at once",
        max_length=8,
        choices=ProjectsAtOnce.choices,
        default=ProjectsAtOnce.PARALLEL,
    )

    class Meta:
        verbose_name = "settings"
        verbose_name_plural = "settings"
        constraints = [
            models.CheckConstraint(
                condition=Q(pk=1), name="settings_singleton"
            ),
        ]

    def __str__(self) -> str:
        """Returns 'Settings'."""
        return "Settings"

    def date_formats(self) -> dict[str, str]:
        """Django date formats for templates, from Date Format.

        date_format is a full date (12/31/2026); short_format a day and
        month (Dec 31 or 31 Dec, in the setting's order), and day_format
        the same with the weekday.
        """
        month_first = self.date_format == self.DateFormat.US
        short = "M j" if month_first else "j M"
        return {
            "date_format": self.date_format,
            "short_format": short,
            "day_format": f"D {short}",
        }

    @classmethod
    def load(cls) -> "AppSettings":
        """Returns the settings row, creating it with defaults."""
        row, _ = cls.objects.get_or_create(pk=1)
        return row
