"""Long Play Time: a project's reels joined into one timeline.

The cue log can give positions (IN, OUT, seq. start and their version
counterparts) on one continuous timeline instead of per reel. It starts
at hour 0 or 1, after an optional leader, and each reel follows the
ones before it by its current picture length (OUT minus IN):

    start + leader + lengths of the reels before + (position - reel IN)

The timeline is the reels the runtime counts: every picture with a
current IN and OUT, in picture order. Cues on the "N/A" placeholder, or
on a reel with no IN and OUT yet, have no place on it; their positions
are left blank. Durations don't change.
"""

from collections.abc import Iterable
import dataclasses

from cues import models
from cues import timecode
from cues.services import progress

START_HOURS = (0, 1)


@dataclasses.dataclass(frozen=True)
class Options:
    """Where the timeline starts.

    Attributes:
        start_hour: 0 or 1: the first reel starts at 00:00:00:00 or
            01:00:00:00, plus the leader.
        leader: Frames before the first reel.
    """

    start_hour: int = 1
    leader: int = 0


@dataclasses.dataclass(frozen=True)
class Reel:
    """A picture and its current IN and OUT (None until a turnover)."""

    picture_id: int
    tc_in: int | None
    tc_out: int | None


def options(start_hour: int, leader: str, rate: timecode.Rate) -> Options:
    """Options from what was typed: an hour and a leader timecode.

    Args:
        start_hour: 0 or 1.
        leader: A duration as a timecode (0:00:08:00) or m:ss; blank for
            none.
        rate: The project's frame rate.

    Returns:
        The options.

    Raises:
        ValueError: On another hour or a leader that isn't a duration,
            with a message to show.
    """
    if start_hour not in START_HOURS:
        raise ValueError("Start at hour 0 or 1.")
    text = leader.strip()
    try:
        frames = timecode.parse_duration(text, rate) if text else 0
    except timecode.TimecodeError as exc:
        raise ValueError(
            "Type the leader as a timecode, e.g. 0:00:08:00."
        ) from exc
    return Options(start_hour=start_hour, leader=frames)


def start(chosen: Options, rate: timecode.Rate) -> int:
    """The frame the first reel starts at: the hour, then the leader."""
    hour = timecode.parse(f"{chosen.start_hour:02d}:00:00:00", rate)
    return hour + chosen.leader


def shifts(pictures: Iterable[Reel], first: int) -> dict[int, int]:
    """What to add to a position in each reel to put it on the timeline.

    Args:
        pictures: The reels in order (`reels`).
        first: Where the first reel starts (`start`).

    Returns:
        Picture id -> frames to add, for the reels with an IN and OUT.
        The others aren't on the timeline and take up no time on it.
    """
    found: dict[int, int] = {}
    elapsed = 0
    for reel in pictures:
        if reel.tc_in is None or reel.tc_out is None:
            continue
        found[reel.picture_id] = first + elapsed - reel.tc_in
        elapsed += reel.tc_out - reel.tc_in
    return found


def place(
    frames: int | None, shift: int | None, rate: timecode.Rate
) -> int | None:
    """A position on the timeline, or None if it has none.

    None when the position or its reel's shift is unknown, or when it
    lands outside 00:00:00:00 to 99:59:59:FF (a cue far before its
    reel's IN on a timeline starting at hour 0).
    """
    if frames is None or shift is None:
        return None
    moved = frames + shift
    return moved if 0 <= moved <= timecode.max_frames(rate) else None


def reels(project: models.Project) -> list[Reel]:
    """The project's pictures in order with their current IN and OUT.

    The "N/A" placeholder is left out: its cues have no reel yet.
    """
    current = progress.current_picture_versions(project)
    versions = models.PictureVersion.objects.in_bulk(list(current.values()))
    found = []
    for picture in project.pictures.exclude(
        name=models.PLACEHOLDER_PICTURE
    ).order_by("order", "name"):
        version = versions.get(current.get(picture.pk, -1))
        found.append(
            Reel(
                picture.pk,
                version.tc_in if version else None,
                version.tc_out if version else None,
            )
        )
    return found
