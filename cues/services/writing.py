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


def logged(cue_ids: Iterable[int]) -> dict[tuple[int, bool], int]:
    """Seconds logged per cue, split into writing (False) and revising."""
    totals: dict[tuple[int, bool], int] = defaultdict(int)
    for cue, revise, seconds in models.WritingLog.objects.filter(
        cue__in=list(cue_ids)
    ).values_list("cue", "revise", "seconds"):
        totals[cue, revise] += seconds
    return dict(totals)


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
    """Records `seconds` of `cue` written on `date`, replacing that day's."""
    revise = bool(cue.status and cue.status.kind in summaries.REVISE_KINDS)
    label = f"Log {summaries.clock(seconds)} of {cue.number} on {_day(date)}"
    with history.recording(cue.project, label, user):
        models.WritingLog.objects.update_or_create(
            cue=cue,
            date=date,
            defaults={"seconds": seconds, "revise": revise},
        )


def remove(cue: models.Cue, date: datetime.date, user: User | None) -> bool:
    """Takes back `date`'s log for `cue`.

    Returns:
        Whether there was one.
    """
    found = models.WritingLog.objects.filter(cue=cue, date=date).first()
    if found is None:
        return False
    label = f"Remove {cue.number}'s log on {_day(date)}"
    with history.recording(cue.project, label, user):
        found.delete()
    return True
