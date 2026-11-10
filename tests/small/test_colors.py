"""A project's colour and its tone (cues.colors)."""

import pytest

from cues import colors


@pytest.mark.parametrize(
    ("project_id", "color", "expected"),
    [
        (5, "blue", 0),
        (5, "magenta", 7),
        (5, "", 5),  # Automatic: from the id
        (13, "", 5),
        (None, "", 0),  # not made yet
        (13, "green", 5),  # not a colour: Automatic
    ],
)
def test_tone(project_id: int | None, color: str, expected: int) -> None:
    assert colors.tone(project_id, color) == expected


def test_automatic_comes_first() -> None:
    assert colors.Color.choices[0] == ("", "Automatic")
    assert len(colors.Color.values) == 9
