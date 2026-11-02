"""Logging how much of a cue was written on a day ("Log Written…").

Each log or removal is one undoable change in the cue's project. Used by
the Log Written dialog and the MCP log_written tool; the schedule counts
the logs as done (cues.services.agenda).
"""

import datetime
import re

from django.utils import dateformat

from accounts.models import User
from cues import models
from cues.services import history
from cues.services import summaries

_AMOUNT = re.compile(
    r"^\s*(?:(?P<m>\d+):(?P<s>\d{1,2})|(?P<min>\d+(?:\.\d+)?))\s*$"
)


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
