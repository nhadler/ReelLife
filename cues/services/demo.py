"""A realistic demo project for manual QA, screenshots and E2E tests.

Deterministic: the same call always builds the same project.
"""

import dataclasses
import datetime
import random

from django.db import transaction

from cues import models
from cues import timecode
from cues.services import projects

PRODUCTION_NUMBER = "DEMO101"
_RATE = timecode.RATE_23_976
_SECOND = _RATE.nominal

_TITLES = (
    "Main Title",
    "Arrival",
    "The Lamp Room",
    "First Storm",
    "Letters",
    "Low Tide",
    "Night Watch",
    "The Wreck",
    "Salvage",
    "Village Market",
    "Fog Bank",
    "Signal Fire",
    "Old Logbook",
    "Departure",
    "Undertow",
    "The Lens",
    "Gull Island",
    "Keeper's Theme",
    "Supply Boat",
    "Rescue",
    "Aftermath",
    "Cold Morning",
    "Inspector",
    "Paper Trail",
    "Riptide",
    "The Bell",
    "Homecoming",
    "Shipwright",
    "Lantern Walk",
    "Squall",
    "Confession",
    "Harbour",
    "Tidal Clock",
    "Beacon",
    "Last Light",
    "Driftwood",
    "Shore Leave",
    "Black Water",
    "Dawn",
    "End Credits",
)
_TEMP = ("Jóhannsson - Flight", "Richter - On the Nature of Daylight", "")


@dataclasses.dataclass(frozen=True)
class DemoSummary:
    """What build_demo created."""

    project: models.Project
    pictures: int
    turnovers: int
    cues: int
    versions: int


def _tc(text: str) -> int:
    return timecode.parse(text, _RATE)


@dataclasses.dataclass(frozen=True)
class _Reel:
    """A reel with its version in the first cut and, if changed, the new."""

    picture: models.Picture
    first_cut: models.PictureVersion
    new_cut: models.PictureVersion | None

    @property
    def head_shift(self) -> int:
        """Frames the reel's head moved in the new cut."""
        if self.new_cut is None:
            return 0
        return (self.new_cut.tc_in or 0) - (self.first_cut.tc_in or 0)


# Status of each cue in turn; some positions are overridden below.
_STATUS_CYCLE = (
    "Approved",
    "Composed",
    "Composed",
    "Revisions",
    "In Progress",
    "To Write",
    "To Write",
    "Completed",
    "Small Revisions",
    "Rewrite",
)
_SPECIAL = {
    9: (models.Cue.CueType.SOURCE, "License"),
    19: (models.Cue.CueType.SONG, "Song"),
    29: (models.Cue.CueType.SOURCE, "License"),
    33: (models.Cue.CueType.SCORE, "Omit"),
}
_UNWRITTEN = ("To Write", "In Progress", "Song", "License")
_CUES_PER_REEL = {"1": 14, "2": 13, "3": 13}


@transaction.atomic
def build_demo(*, replace: bool = False) -> DemoSummary:
    """Builds the demo project: 3 reels, 2 turnovers, 40 cues, versions.

    Reels 2 and 3 change in the second turnover (reel 2's head moves
    2 s 12 f), so cues written to the first one show up as needing
    conform.

    Args:
        replace: Delete an existing demo project first.

    Returns:
        A summary of what was created.

    Raises:
        ValueError: If the demo project exists and replace is False.
    """
    existing = models.Project.objects.filter(
        production_number=PRODUCTION_NUMBER
    )
    if existing.exists():
        if not replace:
            raise ValueError("The demo project exists; use --replace.")
        for project in existing:
            # Cues protect their pictures, so remove cues first.
            project.cues.all().delete()
            project.delete()

    project = _project()
    reels = _reels(project)
    cues, versions = _cues(project, reels)
    return DemoSummary(project, len(reels), 2, cues, versions)


def _project() -> models.Project:
    project = projects.create_project(
        title="The Lighthouse Keeper",
        production_number=PRODUCTION_NUMBER,
        frame_rate="23.976",
        production_company="Northlight Pictures",
        country="US",
    )
    for kind, end in (
        ("composition", datetime.date(2026, 12, 4)),
        ("mix", datetime.date(2026, 12, 18)),
        ("delivery", datetime.date(2027, 1, 8)),
    ):
        models.Deadline.objects.create(project=project, kind=kind, end_date=end)
    return project


def _reels(project: models.Project) -> list[_Reel]:
    first = models.Turnover.objects.create(
        project=project,
        label="c0917",
        received_date=datetime.date(2026, 9, 17),
        notes="First cut for spotting.",
    )
    second = models.Turnover.objects.create(
        project=project,
        label="c1030",
        received_date=datetime.date(2026, 10, 30),
        notes="Reel 2 head trimmed; reel 3 recut.",
    )
    # (first-cut OUT, new-cut IN/OUT or None) per reel; reel 1 unchanged.
    layout = {
        "1": ("1:20:12:00", None),
        "2": ("2:21:12:00", ("2:00:02:12", "2:21:14:12")),
        "3": ("3:22:12:00", ("3:00:00:00", "3:21:40:00")),
    }
    reels = []
    for order, (name, (first_out, new_cut)) in enumerate(layout.items(), 1):
        picture = models.Picture.objects.create(
            project=project, name=name, order=order
        )
        first_version = models.PictureVersion.objects.create(
            picture=picture,
            turnover=first,
            tc_in=_tc(f"{name}:00:00:00"),
            tc_out=_tc(first_out),
        )
        new_version = None
        if new_cut is not None:
            new_version = models.PictureVersion.objects.create(
                picture=picture,
                turnover=second,
                tc_in=_tc(new_cut[0]),
                tc_out=_tc(new_cut[1]),
            )
        reels.append(_Reel(picture, first_version, new_version))
    return reels


def _cues(project: models.Project, reels: list[_Reel]) -> tuple[int, int]:
    # Demo data, not security: a fixed seed keeps it reproducible.
    rng = random.Random(1030)  # noqa: S311  # nosec B311
    statuses = {s.label: s for s in project.statuses.all()}
    sub_statuses = list(project.sub_statuses.all())
    cues = versions = 0
    for reel in reels:
        # Cues were spotted on the first cut; where the new cut moved the
        # reel's head, their current timecodes move with it.
        position = (reel.first_cut.tc_in or 0) + 20 * _SECOND
        for n in range(1, _CUES_PER_REEL[reel.picture.name] + 1):
            cue_type, label = _SPECIAL.get(
                cues,
                (models.Cue.CueType.SCORE, _STATUS_CYCLE[cues % 10]),
            )
            length = rng.randrange(20, 180) * _SECOND + rng.randrange(_SECOND)
            spotted_in = position
            position += length + rng.randrange(15, 60) * _SECOND
            # Drawn here, where the cue used to take it, so the seeded
            # data stays the same.
            notes = {
                "temp": rng.choice(_TEMP),
                "spotting_notes": "Starts under the dialogue; out on the cut.",
            }
            cue = models.Cue.objects.create(
                project=project,
                picture=reel.picture,
                number=f"{reel.picture.name}m{n:02d}",
                title=_TITLES[cues],
                cue_type=cue_type,
                status=statuses[label],
                tc_in=spotted_in + reel.head_shift,
                tc_out=spotted_in + reel.head_shift + length,
                lane=n % 2,
                active=label != "Omit",
                usage=(
                    models.Cue.Usage.BI
                    if cue_type == models.Cue.CueType.SCORE
                    else models.Cue.Usage.VI
                ),
                usage_theme="MT" if cues == 0 else "",
            )
            cues += 1
            # Drawn in the same order as when these were tags, so the
            # seeded data stays the same.
            sub_status = (
                rng.choice(sub_statuses) if rng.random() < 0.25 else None
            )
            if label in _UNWRITTEN:
                versions += _spotted(cue, reel, notes)
            else:
                versions += _versions(cue, reel, spotted_in, length, rng, notes)
            if sub_status is not None:
                version = cue.versions.latest("created_at")
                version.sub_status = sub_status
                version.save()  # via the model, so history has it
    return cues, versions


def _spotted(cue: models.Cue, reel: _Reel, notes: dict[str, str]) -> int:
    """v1 for a cue that isn't written yet, on today's picture."""
    cue.current_version = models.CueVersion.objects.create(
        cue=cue,
        label="v1",
        written_to=reel.new_cut or reel.first_cut,
        tc_in=cue.tc_in,
        tc_out=cue.tc_out,
        **notes,
    )
    cue.save(update_fields=["current_version", "updated_at"])
    return 1


def _versions(
    cue: models.Cue,
    reel: _Reel,
    spotted_in: int,
    length: int,
    rng: random.Random,
    notes: dict[str, str],
) -> int:
    """Adds v1 on the first cut, and on changed reels sometimes a v2."""
    review = (
        "approved"
        if cue.status and cue.status.label == "Approved"
        else ("needs_review")
    )
    current = models.CueVersion.objects.create(
        cue=cue,
        label="v1",
        written_to=reel.first_cut,
        tc_in=spotted_in,
        tc_out=spotted_in + length,
        review_status=review,
        **notes,
    )
    count = 1
    # The rest stay on old picture and so need conform.
    if reel.new_cut is not None and rng.random() < 0.4:
        current = models.CueVersion.objects.create(
            cue=cue,
            label="v2",
            written_to=reel.new_cut,
            tc_in=cue.tc_in,
            tc_out=cue.tc_out,
            change_notes="Re-timed to c1030.",
            review_status=review,
            **notes,
        )
        count = 2
    cue.current_version = current
    cue.save(update_fields=["current_version", "updated_at"])
    return count
