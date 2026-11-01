"""Progress and conform maths."""

import pytest

from cues.services import progress
from cues.timecode import RATE_23_976

Facts = progress.CueFacts


def _progress(*cues: Facts, songs: bool = False) -> progress.Progress:
    return progress.progress(cues, include_songs_and_licensed=songs)


def test_matches_the_documented_header_example() -> None:
    # 1m01 composed (1:25), 1m02 to write (2:12): 39% by duration.
    result = _progress(Facts(2_040, "done", True), Facts(3_176, "to_do", True))
    assert result.percent == 39
    assert result.summary(RATE_23_976) == (
        "Composed 1 / 2 cues (39%)  1:25 of 3:37",
        "Remaining 1 cue (2:12)",
    )


@pytest.mark.parametrize("kind", ["changes", "done", "approved", "completed"])
def test_composed_kinds(kind: str) -> None:
    assert _progress(Facts(100, kind, True)).composed.cues == 1


@pytest.mark.parametrize("kind", ["to_do", "in_progress", "redo"])
def test_unfinished_kinds_count_in_total_only(kind: str) -> None:
    result = _progress(Facts(100, kind, True))
    assert (result.composed.cues, result.total.cues) == (0, 1)


@pytest.mark.parametrize("kind", ["on_hold", "omit"])
def test_inactive_kinds_never_count(kind: str) -> None:
    assert _progress(Facts(100, kind, True)).total.cues == 0


def test_inactive_cues_never_count() -> None:
    assert _progress(Facts(100, "done", False)).total.cues == 0


@pytest.mark.parametrize("kind", ["song", "license"])
def test_songs_and_licensed_follow_the_setting(kind: str) -> None:
    assert _progress(Facts(100, kind, True)).total.cues == 0
    with_songs = _progress(Facts(100, kind, True), songs=True)
    assert (with_songs.total.cues, with_songs.composed.cues) == (1, 0)


def test_unknown_kind_and_negative_duration() -> None:
    result = _progress(Facts(100, "mystery", True), Facts(-5, "done", True))
    assert result.total == progress.Totals(1, 0)


def test_empty_project() -> None:
    result = _progress()
    assert result.percent == 0
    assert result.summary(RATE_23_976) == (
        "Composed 0 / 0 cues (0%)  0:00 of 0:00",
        "Remaining 0 cues (0:00)",
    )


def test_percent_rounds_half_up() -> None:
    result = _progress(Facts(1, "done", True), Facts(7, "to_do", True))
    assert result.percent == 13  # 12.5% -> 13


def test_conform_on_newer_picture() -> None:
    result = progress.conform(
        written_to_id=1,
        current_picture_version_id=2,
        cue_in=1_060,
        cue_out=2_000,
        version_in=1_000,
        version_out=1_964,
    )
    assert result == progress.Conform(True, 60, -24)


def test_conform_on_current_picture() -> None:
    result = progress.conform(
        written_to_id=2,
        current_picture_version_id=2,
        cue_in=10,
        cue_out=20,
        version_in=10,
        version_out=20,
    )
    assert result == progress.Conform(False, 0, 0)


@pytest.mark.parametrize(
    ("written_to", "current"), [(None, 2), (1, None), (None, None)]
)
def test_no_conform_without_both_picture_versions(
    written_to: int | None, current: int | None
) -> None:
    result = progress.conform(
        written_to_id=written_to,
        current_picture_version_id=current,
        cue_in=None,
        cue_out=None,
        version_in=None,
        version_out=None,
    )
    assert result == progress.Conform(False, None, None)


def test_offset_without_outs() -> None:
    result = progress.conform(
        written_to_id=1,
        current_picture_version_id=1,
        cue_in=5,
        cue_out=None,
        version_in=3,
        version_out=None,
    )
    assert (result.start_offset, result.duration_difference) == (2, None)


def test_details_are_the_header_figures_without_labels() -> None:
    result = _progress(Facts(2_040, "done", True), Facts(3_176, "to_do", True))
    assert result.details(RATE_23_976) == (
        "1 / 2 cues (39%)",
        "1:25 of 3:37",
        "1 cue (2:12)",
    )


def test_bar_puts_composed_first_and_shares_the_total() -> None:
    result = _progress(
        Facts(100, "to_do", True),
        Facts(300, "approved", True),
        Facts(100, "completed", True),
        Facts(200, "changes", True),
        Facts(300, "done", True),
        Facts(50, "omit", True),  # never counted
    )
    segments = result.segments()
    assert [(s.label, s.css, s.frames, s.percent) for s in segments] == [
        ("Approved", "approved", 400, 40),
        ("Composed", "done", 300, 30),
        ("Revisions", "changes", 200, 20),
        ("To Write", "to-do", 100, 10),
    ]
    assert [(s.start, s.width) for s in segments] == [
        (0, 40),
        (40, 30),
        (70, 20),
        (90, 10),
    ]


def test_bar_includes_songs_when_they_count() -> None:
    cues = (Facts(100, "song", True), Facts(100, "license", True))
    assert not _progress(*cues).segments()
    labels = [s.label for s in _progress(*cues, songs=True).segments()]
    assert labels == ["Original Songs", "Licensed"]


def test_bar_is_empty_without_music() -> None:
    assert not _progress(Facts(0, "to_do", True)).segments()
