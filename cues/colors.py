"""A project's colour (app.css --tone-0 to --tone-7).

It tints the project's pages and marks the project on the Schedule, the
Overview and Projects.
"""

from django.db import models


class Color(models.TextChoices):
    """The colours a project can be, in tone order; Automatic picks one."""

    AUTOMATIC = "", "Automatic"
    BLUE = "blue", "Blue"
    ROSE = "rose", "Rose"
    TEAL = "teal", "Teal"
    VIOLET = "violet", "Violet"
    SKY = "sky", "Sky"
    PINK = "pink", "Pink"
    INDIGO = "indigo", "Indigo"
    MAGENTA = "magenta", "Magenta"


_TONES = tuple(value for value in Color.values if value)


def tone(project_id: int | None, color: str) -> int:
    """A project's tone, 0-7.

    The chosen colour's place among the colours or, for Automatic, one
    picked from the project's id, so projects made in turn differ.
    """
    if color in _TONES:
        return _TONES.index(color)
    return (project_id or 0) % len(_TONES)
