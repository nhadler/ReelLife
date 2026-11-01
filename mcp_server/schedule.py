"""The Schedule over MCP: what to write when, and pinning cues to days."""

import datetime
from typing import Any

from django.utils import timezone
from mcp.server.mcpserver.exceptions import ToolError

from accounts.models import Role
from cues import models
from cues.services import agenda as agenda_service
from cues.services import pins
from cues.services.summaries import clock
from cues.views import schedule as schedule_view
from mcp_server import common

# The longest range one call returns, in days.
MAX_DAYS = 92


def _date(text: str, name: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(text.strip())
    except ValueError as exc:
        raise ToolError(f"{name}: use a date like 2026-11-05.") from exc


def schedule(start: str = "", end: str = "") -> dict[str, Any]:
    """What to write each day: today by default, or start to end.

    Dates are YYYY-MM-DD (at most 92 days at once). Answers "what should
    I write today?". Past days show what was planned on them. Each cue
    has that day's music and, for a long cue split across days, all it
    has left.
    """
    common.user(Role.VIEWER)
    today = timezone.localdate()
    first = _date(start, "start") if start else today
    last = _date(end, "end") if end else first
    if last < first:
        raise ToolError("end is before start.")
    if (last - first).days >= MAX_DAYS:
        raise ToolError(f"Ask for at most {MAX_DAYS} days at once.")
    agenda = agenda_service.build(today)
    formats = models.AppSettings.load().date_formats()
    line, warnings = schedule_view.summary(agenda, None, formats)
    days = []
    for day in schedule_view.days_view(agenda, first, last, None):
        if not day.parts and not day.off:
            continue
        days.append(
            {
                "date": day.date.isoformat(),
                "music": day.music,
                "days_off": day.off,
                "cues": [
                    {
                        "cue_id": p.cue.id,
                        "number": p.cue.number,
                        "title": p.cue.title,
                        "project": p.project.title,
                        "music": clock(round(p.part.music)),
                        "of_music": clock(round(p.part.of_music)),
                        "pinned": p.part.pinned,
                        "revision": p.cue.revise,
                        "difficulty": p.cue.difficulty,
                    }
                    for p in day.parts
                ],
            }
        )
    return {
        "today": today.isoformat(),
        "summary": line,
        "warnings": warnings,
        "days": days,
    }


def pin_cue(cue_id: int, date: str = "") -> dict[str, Any]:
    """Fixes a cue to a day of the schedule (YYYY-MM-DD), or unpins it.

    With no date it unpins; the rest of the plan flows around a pin.
    Undoable.
    """
    user = common.user(Role.EDITOR)
    cue = common.cue(cue_id)
    if not date.strip():
        return {"cue": cue.number, "unpinned": pins.unpin(cue, user)}
    day = _date(date, "date")
    if day < timezone.localdate():
        raise ToolError("Pick today or a later day.")
    pins.pin(cue, day, user)
    return {"cue": cue.number, "pinned_to": day.isoformat()}


READ_TOOLS = (schedule,)
TOOLS = (pin_cue,)
