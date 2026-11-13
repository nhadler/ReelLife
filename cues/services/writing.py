"""Logging how much of a cue was written on a day ("Log Written…").

Each log or removal is one undoable change in the cue's project. Used by
the Log Written dialog and the MCP log_written tool; the schedule counts
the logs as done (cues.services.agenda), and the grid's % Written column
shows them against the cue's length (`progress`).
"""

from collections import defaultdict
from collections.abc import Iterable
import dataclasses
import datetime
import math
import re

from django.utils import dateformat

from accounts.models import User
from cues import models
from cues import timecode
from cues.services import history
from cues.services import summaries

_AMOUNT = re.compile(
    r"^\s*(?:(?P<m>\d+):(?P<s>\d{1,2})|(?P<min>\d+(?:\.\d+)?))\s*$"
)


# A cue in these is written: 100%.
DONE_KINDS = frozenset(
    {
        models.StatusKind.DONE,
        models.StatusKind.APPROVED,
        models.StatusKind.COMPLETED,
    }
)


@dataclasses.dataclass(frozen=True)
class Progress:
    """How much of a cue is written, for the grid's % Written column.

    Attributes:
        percent: 0 to 100; 100 only once the cue is marked written.
        stage: 'write', 'revise' or 'done'.
        written: Seconds logged for the current work (None when done).
        music: The cue's length in seconds (None when done).
    """

    percent: int
    stage: str
    written: int | None = None
    music: int | None = None


def progress(kind: str, music: float | None, written: int) -> Progress | None:
    """A cue's progress from its status kind, length and logged seconds.

    Written (Composed, Approved, Completed) is 100%. Writing and revising
    count what's logged for that work against the cue's length, as the
    schedule does, so revisions start again from 0%; logged work stops at
    99% until the cue is marked written. Nothing for cues that aren't
    written at all (On Hold, Omit, songs and licensed music), nor for one
    without an IN and OUT, whose length isn't known.

    Args:
        kind: The cue's status kind ('' with no status counts as To Do).
        music: The cue's length in seconds, or None without IN and OUT.
        written: Seconds logged for the cue's current kind of work.
    """
    if kind in DONE_KINDS:
        return Progress(100, "done")
    kind = kind or models.StatusKind.TO_DO
    if kind in summaries.WRITE_KINDS:
        stage = "write"
    elif kind in summaries.REVISE_KINDS:
        stage = "revise"
    else:
        return None
    if not music or music <= 0:
        return None
    percent = min(math.floor(written * 100 / music), 99)
    if written and not percent:
        percent = 1  # some written shows, however little
    return Progress(percent, stage, written, round(music))


@dataclasses.dataclass(frozen=True)
class Coverage:
    """How much of one kind of work on a cue is logged.

    Attributes:
        seconds: Logged music: the stretches, overlaps counted once, plus
            amounts logged without a stretch.
        end: Frames after the cue's IN where the next log starts: past
            the furthest stretch, or past the amounts laid end to end.
    """

    seconds: int
    end: int


# A log's stretch (frames after IN, or None) and seconds.
Entry = tuple[int | None, int | None, int]


def coverage(entries: Iterable[Entry], rate: timecode.Rate) -> Coverage:
    """What `entries` (one cue, one kind of work) log, at `rate`."""
    entries = list(entries)
    stretches = sorted(
        (start, end)
        for start, end, _ in entries
        if start is not None and end is not None
    )
    amounts = sum(seconds for start, _, seconds in entries if start is None)
    frames = 0
    furthest = 0
    for start, end in stretches:
        if end <= furthest:
            continue
        frames += end - max(start, furthest)
        furthest = end
    seconds = round(timecode.seconds(frames, rate)) + amounts
    per_second = rate.fps if rate.drop_frame else rate.nominal
    laid = math.floor(amounts * per_second)
    return Coverage(seconds, max(furthest, laid))


def _entries(
    cue_ids: Iterable[int],
) -> dict[tuple[int, bool], list[Entry]]:
    """Each cue's logs as entries, split into writing and revising."""
    found: dict[tuple[int, bool], list[Entry]] = defaultdict(list)
    for cue, revise, start, end, seconds in models.WritingLog.objects.filter(
        cue__in=list(cue_ids)
    ).values_list("cue", "revise", "start", "end", "seconds"):
        found[cue, revise].append((start, end, seconds))
    return found


def logged(
    cue_ids: Iterable[int], rate: timecode.Rate
) -> dict[tuple[int, bool], int]:
    """Seconds logged per cue, split into writing (False) and revising.

    Overlapping stretches count once. `rate` is the cues' project's.
    """
    return {
        key: coverage(entries, rate).seconds
        for key, entries in _entries(cue_ids).items()
    }


def revising(cue: models.Cue) -> bool:
    """Whether the cue's work now is revising (logged apart)."""
    return bool(cue.status and cue.status.kind in summaries.REVISE_KINDS)


def next_start(cue: models.Cue) -> int:
    """Frames after the cue's IN where its next log starts."""
    entries = _entries([cue.pk]).get((cue.pk, revising(cue)), [])
    return coverage(entries, cue.project.rate).end


def logs(cue: models.Cue) -> list[models.WritingLog]:
    """The cue's logs for its work now, oldest first."""
    return list(cue.writing_logs.filter(revise=revising(cue)))


class AmountError(ValueError):
    """Text that isn't an amount of music."""


def parse_amount(text: str) -> int:
    """Seconds from '1:20' (m:ss) or '1.5' (minutes).

    Raises:
        AmountError: Neither form, or seconds past 59.
    """
    match = _AMOUNT.match(text)
    if match is None:
        raise AmountError("Enter an amount like 1:20, or minutes like 1.5.")
    if match["min"] is not None:
        return round(float(match["min"]) * 60)
    if int(match["s"]) >= 60:
        raise AmountError("Seconds must be under 60.")
    return int(match["m"]) * 60 + int(match["s"])


def _day(date: datetime.date) -> str:
    short = models.AppSettings.load().date_formats()["short_format"]
    return dateformat.format(date, short)


def log(
    cue: models.Cue, date: datetime.date, seconds: int, user: User | None
) -> None:
    """Records `seconds` of `cue` written on `date`, without a stretch."""
    label = f"Log {summaries.clock(seconds)} of {cue.number} on {_day(date)}"
    with history.recording(cue.project, label, user):
        models.WritingLog.objects.create(
            cue=cue, date=date, seconds=seconds, revise=revising(cue)
        )


class StretchError(ValueError):
    """A stretch that isn't inside the cue, or runs backwards."""


def log_stretch(
    cue: models.Cue,
    date: datetime.date,
    start: int,
    end: int,
    user: User | None,
) -> models.WritingLog:
    """Records that `cue` was written from `start` to `end` on `date`.

    Args:
        cue: The cue, with an IN and OUT.
        date: The day it was written.
        start: Frames after the cue's IN.
        end: Frames after the cue's IN, past `start`.
        user: Who logged it, for History.

    Raises:
        StretchError: The cue has no IN and OUT, or the stretch isn't
            inside them or runs backwards.
    """
    if cue.tc_in is None or cue.tc_out is None:
        raise StretchError(f"Give {cue.number} an IN and OUT first.")
    if not 0 <= start < end <= cue.tc_out - cue.tc_in:
        raise StretchError(
            "Pick a stretch inside the cue's IN and OUT, From before To."
        )
    rate = cue.project.rate
    seconds = round(timecode.seconds(end - start, rate))
    shown = " to ".join(
        timecode.format_timecode(cue.tc_in + frames, rate)
        for frames in (start, end)
    )
    label = f"Log {shown} of {cue.number} on {_day(date)}"
    with history.recording(cue.project, label, user):
        return models.WritingLog.objects.create(
            cue=cue,
            date=date,
            seconds=seconds,
            start=start,
            end=end,
            revise=revising(cue),
        )


def remove(entry: models.WritingLog, user: User | None) -> None:
    """Takes back one log."""
    cue = entry.cue
    label = f"Remove {cue.number}'s log on {_day(entry.date)}"
    with history.recording(cue.project, label, user):
        entry.delete()


def remove_day(cue: models.Cue, date: datetime.date, user: User | None) -> int:
    """Takes back every log of `cue` on `date`.

    Returns:
        How many there were.
    """
    found = list(models.WritingLog.objects.filter(cue=cue, date=date))
    if found:
        label = f"Remove {cue.number}'s logs on {_day(date)}"
        with history.recording(cue.project, label, user):
            for entry in found:
                entry.delete()
    return len(found)
