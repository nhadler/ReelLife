"""The conform assistant.

After a turnover, a reel whose IN moved usually just had its head
trimmed or extended: every cue in it moves by the same amount. The
assistant offers that shift per reel, then lists the cues still on older
picture so each can be marked conformed, by moving its current version
to the new picture or by adding a new version there. Each step is one
change set, so Undo reverts it.

A shift is applied once: the new picture version records it
(`cues_shifted`, cleared by undo), so a second click or a retried MCP
call does nothing more. It moves only cues whose current version was
written to the reel's earlier picture; cues already placed on the new
cut (imported or added there) stay put.
"""

from collections.abc import Sequence
import dataclasses

from django.db.models import Q

from accounts.models import User
from cues import models
from cues import timecode
from cues.services import authoring
from cues.services import history
from cues.services import progress
from cues.services import tools


@dataclasses.dataclass(frozen=True)
class ReelMove:
    """A reel whose IN moved in a turnover."""

    picture: models.Picture
    before: models.PictureVersion
    after: models.PictureVersion
    delta: int  # frames; positive is later
    cues: int  # active cues still to shift; 0 once shifted

    def label(self, rate: timecode.Rate) -> str:
        """e.g. '+00:00:02:12'."""
        return timecode.format_offset(self.delta, rate)


def _earlier(after: models.PictureVersion) -> Q:
    """Picture versions of turnovers before `after`'s, same-day ones by id."""
    turnover = after.turnover
    return Q(turnover__received_date__lt=turnover.received_date) | Q(
        turnover__received_date=turnover.received_date,
        turnover_id__lt=turnover.pk,
    )


def _to_shift(
    picture: models.Picture, after: models.PictureVersion
) -> list[models.Cue]:
    """The reel's active cues on its earlier picture, unless shifted."""
    if after.cues_shifted:
        return []
    earlier = set(
        picture.versions.filter(_earlier(after)).values_list("pk", flat=True)
    )
    return [
        cue
        for cue in picture.cues.filter(active=True).select_related(
            "current_version"
        )
        if cue.current_version is not None
        and cue.current_version.written_to_id in earlier
    ]


def moved_reels(turnover: models.Turnover) -> list[ReelMove]:
    """The turnover's reels whose IN differs from the picture before it."""
    moves = []
    for after in turnover.picture_versions.select_related(
        "picture", "turnover"
    ).order_by("picture__order", "picture__name"):
        before = (
            after.picture.versions.filter(_earlier(after))
            .order_by("-turnover__received_date", "-turnover_id")
            .first()
        )
        if (
            before is None
            or before.tc_in is None
            or after.tc_in is None
            or before.tc_in == after.tc_in
        ):
            continue
        moves.append(
            ReelMove(
                picture=after.picture,
                before=before,
                after=after,
                delta=after.tc_in - before.tc_in,
                cues=len(_to_shift(after.picture, after)),
            )
        )
    return moves


def shift_reels(
    project: models.Project,
    moves: Sequence[ReelMove],
    user: User | None = None,
) -> int:
    """Shifts each reel's cues not yet shifted by its move, as one change set.

    Returns:
        How many cues moved.

    Raises:
        tools.ToolError: A cue would move outside the timecode range.
    """
    plans = [
        (move, _to_shift(move.picture, move.after))
        for move in moves
        if not move.after.cues_shifted
    ]
    if not plans:
        return 0  # each was shifted already
    for move, cues in plans:
        tools.check_shift(cues, move.delta)
    names = ", ".join(move.picture.title for move, _ in plans)
    moved = 0
    with history.recording(project, f"Conform: shift {names}", user):
        for move, cues in plans:
            move.after.cues_shifted = True
            move.after.save()
            for cue in cues:
                cue.tc_in, cue.tc_out, cue.seq_start = tools.shifted(
                    cue, move.delta
                )
                cue.full_clean()
                cue.save()
                moved += 1
    return moved


@dataclasses.dataclass(frozen=True)
class Stale:
    """A cue whose current version was written to older picture."""

    cue: models.Cue
    written_to: str
    start_offset: str
    duration_difference: str


def needing_conform(project: models.Project) -> list[Stale]:
    """Active cues on older picture, in picture order."""
    current = progress.current_picture_versions(project)
    rate = project.rate
    stale = []
    cues = project.cues.filter(active=True, current_version__isnull=False)
    for cue in cues.select_related(
        "picture", "current_version__written_to__turnover"
    ).order_by("picture__order", "picture__name", "tc_in", "number"):
        version = cue.current_version
        if version is None:  # pragma: no cover - filtered above
            continue
        result = progress.conform(
            written_to_id=version.written_to_id,
            current_picture_version_id=current.get(cue.picture_id),
            cue_in=cue.tc_in,
            cue_out=cue.tc_out,
            version_in=version.tc_in,
            version_out=version.tc_out,
        )
        if not result.needs_conform:
            continue
        stale.append(
            Stale(
                cue=cue,
                written_to=str(version.written_to.turnover)
                if version.written_to
                else "",
                start_offset=""
                if result.start_offset is None
                else timecode.format_offset(result.start_offset, rate),
                duration_difference=""
                if result.duration_difference is None
                else timecode.format_offset(result.duration_difference, rate),
            )
        )
    return stale


def mark_conformed(
    project: models.Project,
    cues: Sequence[models.Cue],
    *,
    new_version: bool,
    user: User | None = None,
) -> None:
    """Brings the cues' versions onto their reels' current picture.

    Args:
        project: The project.
        cues: Cues on older picture.
        new_version: Add a new version written to the current picture, at
            the cue's timecodes; otherwise move the current version there
            (its timecodes become the cue's).
        user: Who did it.
    """
    noun = "cue" if len(cues) == 1 else "cues"
    with history.recording(
        project, f"Conform: mark {len(cues)} {noun} conformed", user
    ):
        for cue in cues:
            picture = cue.picture.current_version()
            if new_version:
                # Like the New Version dialog: it keeps the current
                # version's notes and sub status.
                previous = cue.current_version
                carried = {
                    name: getattr(previous, name)
                    for name in (*models.VERSION_NOTE_FIELDS, "sub_status")
                    if previous is not None
                }
                authoring.save_version(
                    models.CueVersion(
                        cue=cue,
                        label=authoring.next_version_label(cue),
                        written_to=picture,
                        tc_in=cue.tc_in,
                        tc_out=cue.tc_out,
                        seq_start=cue.seq_start,
                        **carried,
                    ),
                    update_cue=False,
                )
                continue
            version = cue.current_version
            if version is None:
                continue
            version.written_to = picture
            version.tc_in, version.tc_out = cue.tc_in, cue.tc_out
            version.seq_start = cue.seq_start
            version.full_clean()
            version.save()
            cue.save()  # bumps updated_at for the grid
