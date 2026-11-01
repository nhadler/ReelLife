"""Timecode filters: frames in, text in the project's frame rate out.

Usage: {% load timecodes %} {{ cue.tc_in|tc:project }}
"""

from django import template

from cues import models
from cues import timecode

register = template.Library()


@register.filter
def tc(frames: int | None, project: models.Project) -> str:
    """A timecode like 1:00:10:00, or '' when unset."""
    if frames is None:
        return ""
    settings = models.AppSettings.load()
    return timecode.format_timecode(
        frames,
        project.rate,
        leading_zero=settings.leading_zero,
        subframes=settings.subframes,
    )
