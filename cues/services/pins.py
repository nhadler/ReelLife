"""Pinning cues to days of the schedule ("Schedule On…").

Each pin, unpin or reset is one undoable change in the project. Used by
the Schedule On dialog, Reset Schedule and the MCP pin_cue tool.
"""

import datetime

from django.utils import dateformat

from accounts.models import User
from cues import models
from cues.services import history


def pin(cue: models.Cue, date: datetime.date, user: User | None) -> None:
    """Fixes `cue` to `date`; the rest of the plan flows around it."""
    short = models.AppSettings.load().date_formats()["short_format"]
    label = f"Schedule {cue.number} on {dateformat.format(date, short)}"
    with history.recording(cue.project, label, user):
        models.SchedulePin.objects.update_or_create(
            cue=cue, defaults={"date": date}
        )


def unpin(cue: models.Cue, user: User | None) -> bool:
    """Lets `cue` flow with the rest again.

    Returns:
        Whether it had a pin.
    """
    found = models.SchedulePin.objects.filter(cue=cue).first()
    if found is None:
        return False
    with history.recording(cue.project, f"Unschedule {cue.number}", user):
        found.delete()
    return True


def reset(project: models.Project, user: User | None) -> int:
    """Unpins every cue in `project`, so its plan is automatic again.

    One undoable change. Logged written work stays.

    Returns:
        How many cues were unpinned.
    """
    found = list(models.SchedulePin.objects.filter(cue__project=project))
    if not found:
        return 0
    with history.recording(project, "Reset Schedule", user):
        for each in found:
            each.delete()
    return len(found)
