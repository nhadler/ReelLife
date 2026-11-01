"""Project notes: general notes and one per kind of cue note.

Decided Oct 31: a project has General notes plus project-wide notes for
each kind of note a cue carries (spotting, notes, revision, conform,
orchestration, recording, mix, cue sheet). Tracked by history, so an
edit can be undone like any other. Re-exported from cues.models.
"""

from django.db import models

from cues.model_base import Timestamped
from cues.model_base import _history


class NoteKind(models.TextChoices):
    """Which notes: general, or the project-wide side of a cue note."""

    GENERAL = "general", "General"
    SPOTTING = "spotting", "Spotting Notes"
    NOTES = "notes", "Notes"
    REVISION = "revision", "Revision Notes"
    CONFORM = "conform", "Conform Notes"
    ORCHESTRATION = "orchestration", "Orchestration Notes"
    RECORDING = "recording", "Recording Notes"
    MIX = "mix", "Mix Notes"
    CUE_SHEET = "cue_sheet", "Cue Sheet Notes"


class ProjectNote(Timestamped):
    """A project's notes of one kind."""

    project = models.ForeignKey(
        "cues.Project", on_delete=models.CASCADE, related_name="notes"
    )
    kind = models.CharField(max_length=20, choices=NoteKind.choices)
    text = models.TextField(blank=True)

    history = _history()

    class Meta:
        ordering = ["project", "kind"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "kind"], name="project_note_unique"
            ),
        ]

    def __str__(self) -> str:
        """Returns e.g. 'Spotting notes'."""
        return NoteKind(self.kind).label
