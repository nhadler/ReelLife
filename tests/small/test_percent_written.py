"""% Written: a cue's progress from its status, length and logged work."""

import pytest

from cues.models import StatusKind
from cues.services import writing
from cues.services.writing import Progress


@pytest.mark.parametrize(
    "kind", [StatusKind.DONE, StatusKind.APPROVED, StatusKind.COMPLETED]
)
def test_a_written_cue_is_100_percent(kind: str) -> None:
    # Whatever was logged, and even without IN and OUT.
    assert writing.progress(kind, None, 0) == Progress(100, "done")
    assert writing.progress(kind, 120.0, 30) == Progress(100, "done")


@pytest.mark.parametrize(
    ("kind", "stage"),
    [
        (StatusKind.TO_DO, "write"),
        (StatusKind.IN_PROGRESS, "write"),
        ("", "write"),  # no status counts as To Do
        (StatusKind.REDO, "revise"),
        (StatusKind.CHANGES, "revise"),
    ],
)
def test_work_counts_what_is_logged(kind: str, stage: str) -> None:
    assert writing.progress(kind, 120.0, 0) == Progress(0, stage, 0, 120)
    assert writing.progress(kind, 120.0, 45) == Progress(37, stage, 45, 120)


def test_a_little_shows_and_a_lot_waits_to_be_marked_written() -> None:
    assert writing.progress(StatusKind.IN_PROGRESS, 600.0, 1) == Progress(
        1, "write", 1, 600
    )
    # Logged in full (or more) but not marked written: 99%.
    assert writing.progress(StatusKind.IN_PROGRESS, 120.0, 120) == Progress(
        99, "write", 120, 120
    )
    assert writing.progress(StatusKind.REDO, 120.0, 500) == Progress(
        99, "revise", 500, 120
    )


@pytest.mark.parametrize(
    "kind",
    [
        StatusKind.ON_HOLD,
        StatusKind.OMIT,
        StatusKind.SONG,
        StatusKind.LICENSE,
    ],
)
def test_cues_that_are_not_written_show_nothing(kind: str) -> None:
    assert writing.progress(kind, 120.0, 60) is None


@pytest.mark.parametrize("music", [None, 0.0, -3.0])
def test_without_a_length_there_is_no_percent(music: float | None) -> None:
    assert writing.progress(StatusKind.IN_PROGRESS, music, 60) is None
