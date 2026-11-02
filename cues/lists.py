"""Themes, instrumentation and department statuses.

Each project has a list of themes and one of instruments; a version uses
themes and a cue uses instruments, each optionally for a duration. The
lists and the link rows are tracked by history, so editing them can be
undone. Re-exported from cues.models.
"""

from django.db import models
from django.db.models.functions import Lower

from cues.model_base import _HEX_COLOR
from cues.model_base import Timestamped
from cues.model_base import _history


class _ListOption(Timestamped):
    """An entry in one of a project's editable lists, with a colour."""

    name = models.CharField(max_length=50)
    color = models.CharField(max_length=7, validators=[_HEX_COLOR])
    order = models.PositiveIntegerField(default=0)

    class Meta:
        abstract = True
        ordering = ["order", "name"]

    def __str__(self) -> str:
        """Returns the name."""
        return self.name


class ThemeOption(_ListOption):
    """A musical theme in the project's list."""

    project = models.ForeignKey(
        "cues.Project", on_delete=models.CASCADE, related_name="themes"
    )

    history = _history()

    class Meta(_ListOption.Meta):
        verbose_name = "theme"
        constraints = [
            models.UniqueConstraint(
                "project",
                Lower("name"),
                name="theme_name_unique",
                violation_error_message="This project already has that theme.",
            ),
        ]


class DepartmentStatus(_ListOption):
    """An entry in one of a project's department status lists.

    Orchestration, Recording, Mix, Music Edit and Licensing each have a
    list per project, editable in settings and seeded from defaults.
    A cue stores the label in its field, so
    renaming an entry renames it on the cues that use it
    (services.department_lists).
    """

    class Field(models.TextChoices):
        ORCHESTRATION = "orchestration_status", "Orchestration Status"
        RECORDING = "recording_status", "Recording Status"
        MIX = "mix_status", "Mix Status"
        MUSIC_EDIT = "music_edit_status", "Music Edit Status"
        LICENSING = "licensing_status", "Licensing Status"

    project = models.ForeignKey(
        "cues.Project",
        on_delete=models.CASCADE,
        related_name="department_statuses",
    )
    field = models.CharField(max_length=20, choices=Field.choices)
    # Cue fields hold up to 30 characters.
    name = models.CharField("label", max_length=30)

    history = _history()

    class Meta(_ListOption.Meta):
        ordering = ["field", "order", "name"]
        verbose_name = "department status"
        verbose_name_plural = "department statuses"
        constraints = [
            models.UniqueConstraint(
                "project",
                "field",
                Lower("name"),
                name="department_status_unique",
                violation_error_message=("This list already has that status."),
            ),
        ]


class InstrumentOption(_ListOption):
    """An instrument or section in the project's list (Instrumentation)."""

    project = models.ForeignKey(
        "cues.Project", on_delete=models.CASCADE, related_name="instruments"
    )

    history = _history()

    class Meta(_ListOption.Meta):
        verbose_name = "instrument"
        constraints = [
            models.UniqueConstraint(
                "project",
                Lower("name"),
                name="instrument_name_unique",
                violation_error_message=(
                    "This project already has that instrument."
                ),
            ),
        ]


class VersionTheme(models.Model):
    """A theme heard in a version, optionally for how long."""

    version = models.ForeignKey(
        "cues.CueVersion", on_delete=models.CASCADE, related_name="themes"
    )
    theme = models.ForeignKey(
        ThemeOption, on_delete=models.CASCADE, related_name="+"
    )
    seconds = models.PositiveIntegerField(null=True, blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    history = _history()

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["version", "theme"], name="version_theme_unique"
            ),
        ]


class CueInstrument(models.Model):
    """An instrument a cue uses, optionally for how long."""

    cue = models.ForeignKey(
        "cues.Cue", on_delete=models.CASCADE, related_name="instrumentation"
    )
    instrument = models.ForeignKey(
        InstrumentOption, on_delete=models.CASCADE, related_name="+"
    )
    seconds = models.PositiveIntegerField(null=True, blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    history = _history()

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["cue", "instrument"], name="cue_instrument_unique"
            ),
        ]
