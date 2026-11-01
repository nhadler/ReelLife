"""Turnovers: picture deliveries covering one or more reels.

Logging, editing and deleting one are each one undoable change.
"""

import dataclasses
import datetime
from typing import cast

from django.db.models import Max

from accounts.models import User
from cues import models
from cues.importers import merge
from cues.services import history
from cues.services import progress
from cues.services import projects


@dataclasses.dataclass(frozen=True)
class ReelEntry:
    """One reel in a turnover: an existing picture, or a new one by name."""

    picture: models.Picture | None
    name: str
    tc_in: int | None
    tc_out: int | None


def create_turnover(
    project: models.Project,
    *,
    label: str,
    received_date: datetime.date,
    notes: str = "",
    reels: list[ReelEntry],
    user: User | None = None,
) -> models.Turnover:
    """Creates a turnover and its picture versions as one undoable change.

    Reels left out simply keep their previous picture version.
    New reel names create pictures, ordered after the existing ones.

    Raises:
        django.core.exceptions.ValidationError: A duplicate label, a reel
            with OUT before IN, or a picture from another project.
    """
    with history.recording(project, f"Turnover {label}", user):
        turnover = models.Turnover(
            project=project,
            label=label,
            received_date=received_date,
            notes=notes,
        )
        turnover.full_clean()
        turnover.save()
        next_order = (
            project.pictures.aggregate(Max("order"))["order__max"] or 0
        ) + 1
        for entry in reels:
            picture = entry.picture
            if picture is None:
                picture = models.Picture(
                    project=project, name=entry.name, order=next_order
                )
                picture.full_clean()
                picture.save()
                next_order += 1
            version = models.PictureVersion(
                picture=picture,
                turnover=turnover,
                tc_in=entry.tc_in,
                tc_out=entry.tc_out,
            )
            version.full_clean()
            version.save()
    return turnover


class TurnoverInUseError(ValueError):
    """Cues were written to picture the change would take away."""


def _written_to(version: models.PictureVersion) -> list[str]:
    """Cue numbers with a version written to this picture."""
    return sorted(
        models.CueVersion.objects.filter(written_to=version)
        .values_list("cue__number", flat=True)
        .distinct()
    )


def _refuse_if_used(version: models.PictureVersion, doing: str) -> None:
    numbers = _written_to(version)
    if numbers:
        raise TurnoverInUseError(
            f"Can't {doing}: {', '.join(numbers[:8])}"
            f"{' and more' if len(numbers) > 8 else ''} "
            f"{'were' if len(numbers) > 1 else 'was'} written to "
            f"{version.picture.title} in {version.turnover}."
        )


def update_turnover(  # pylint: disable=too-many-arguments
    turnover: models.Turnover,
    *,
    label: str,
    received_date: datetime.date,
    notes: str = "",
    reels: list[ReelEntry],
    user: User | None = None,
) -> models.Turnover:
    """Changes a turnover's details and reels, as one undoable change.

    `reels` is every reel the turnover now covers: their IN and OUT are
    set, new ones added; reels no longer listed are dropped from it.

    Raises:
        django.core.exceptions.ValidationError: A duplicate label, or a
            reel with OUT before IN.
        TurnoverInUseError: Dropping a reel that cues were written to.
    """
    project = turnover.project
    with history.recording(project, f"Edit turnover {label}", user):
        turnover.label = label
        turnover.received_date = received_date
        turnover.notes = notes
        turnover.full_clean()
        turnover.save()
        kept = {e.picture.pk for e in reels if e.picture is not None}
        for version in turnover.picture_versions.select_related("picture"):
            if version.picture_id not in kept:
                _refuse_if_used(version, f"drop {version.picture.title}")
                version.delete()
        existing = {v.picture_id: v for v in turnover.picture_versions.all()}
        next_order = (
            project.pictures.aggregate(Max("order"))["order__max"] or 0
        ) + 1
        for entry in reels:
            picture = entry.picture
            if picture is None:
                picture = models.Picture(
                    project=project, name=entry.name, order=next_order
                )
                picture.full_clean()
                picture.save()
                next_order += 1
            version = existing.get(picture.pk) or models.PictureVersion(
                picture=picture, turnover=turnover
            )
            version.tc_in, version.tc_out = entry.tc_in, entry.tc_out
            version.full_clean()
            version.save()
    return turnover


def delete_turnover(
    turnover: models.Turnover, user: User | None = None
) -> None:
    """Deletes a turnover nothing was written to, as one undoable change.

    Raises:
        TurnoverInUseError: Cues were written to its picture.
    """
    for version in turnover.picture_versions.select_related("picture"):
        _refuse_if_used(version, f"delete {turnover}")
    with history.recording(
        turnover.project, f"Delete turnover {turnover}", user
    ):
        for version in turnover.picture_versions.all():
            version.delete()
        turnover.delete()


@dataclasses.dataclass(frozen=True)
class Placement:
    """A cue on the "N/A" reel and the reel its IN falls in."""

    cue: models.Cue
    picture: models.Picture


def unplaced(project: models.Project) -> list[Placement]:
    """Active cues on "N/A" whose IN now lies in a reel's current range.

    Cues imported before any turnover land on the placeholder reel; once
    a turnover gives reels their timecodes, this says where each goes.
    """
    placeholder = project.pictures.filter(
        name=projects.PLACEHOLDER_PICTURE
    ).first()
    if placeholder is None:
        return []
    ranges: list[tuple[int, int, int]] = []  # (reel id, IN, OUT)
    for picture_id, start, end in merge.picture_ranges(project):
        if picture_id != placeholder.pk and None not in (start, end):
            ranges.append((picture_id, cast(int, start), cast(int, end)))
    reels = project.pictures.in_bulk(
        [picture_id for picture_id, _, _ in ranges]
    )
    found = []
    for cue in placeholder.cues.filter(
        active=True, tc_in__isnull=False
    ).order_by("tc_in", "number"):
        for picture_id, start, end in ranges:
            if start <= cast(int, cue.tc_in) <= end:  # filtered above
                found.append(Placement(cue, reels[picture_id]))
                break
    return found


def place(
    project: models.Project,
    placements: list[Placement],
    user: User | None = None,
) -> int:
    """Moves the cues to their reels, as one undoable change.

    A version with no picture yet is written to the reel's current one,
    so the cue isn't then reported as on older picture.

    Returns:
        How many cues moved.
    """
    current = progress.current_picture_versions(project)
    noun = "cue" if len(placements) == 1 else "cues"
    with history.recording(
        project,
        f"Place {len(placements)} {noun} on {project.reels_word.lower()}",
        user,
    ):
        for placement in placements:
            cue = placement.cue
            cue.picture = placement.picture
            cue.full_clean()
            cue.save()
            version = cue.current_version
            if version is not None and version.written_to_id is None:
                version.written_to_id = current.get(placement.picture.pk)
                version.save()
    return len(placements)
