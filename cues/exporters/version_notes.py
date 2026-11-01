"""The version notes PDF.

Each cue's change, revision and general notes, for its current version
or every version, grouped by reel, for an orchestrator or director.
"""

from collections.abc import Iterable
import dataclasses
import datetime

from cues import models
from cues.exporters import pdf
from cues.exporters import wall_chart


@dataclasses.dataclass(frozen=True)
class Note:
    """One version's notes."""

    label: str
    written_to: str
    change_notes: str
    revision_notes: str
    notes: str


def to_pdf(
    project: models.Project,
    *,
    all_versions: bool = False,
    cue_ids: Iterable[int] | None = None,
    today: datetime.date | None = None,
) -> bytes:
    """The version notes PDF: cues with something noted, by reel."""
    reels: dict[str, list[tuple[models.Cue, list[Note]]]] = {}
    for cue in wall_chart.ordered_cues(project, cue_ids):
        versions = (
            sorted(cue.versions.all(), key=lambda v: (v.created_at, v.pk))
            if all_versions
            else [v for v in [cue.current_version] if v is not None]
        )
        notes = [
            Note(
                label=v.label,
                written_to=wall_chart.turnover(v),
                change_notes=v.change_notes.strip(),
                revision_notes=v.revision_notes.strip(),
                notes=v.notes.strip(),
            )
            for v in versions
            if v.change_notes.strip()
            or v.revision_notes.strip()
            or v.notes.strip()
        ]
        if notes:
            reels.setdefault(wall_chart.reel(cue), []).append((cue, notes))
    return pdf.render(
        "cues/pdf/version_notes.html",
        {
            "project": project,
            "reels": reels,
            "all_versions": all_versions,
            "today": today,
        },
    )
