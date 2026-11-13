"""How much of a cue its logs cover, and where the next log starts."""

from cues import timecode
from cues.services.writing import Coverage
from cues.services.writing import coverage

FPS24 = timecode.rate_for("24")
SECOND = 24


def test_nothing_logged() -> None:
    assert coverage([], FPS24) == Coverage(0, 0)


def test_stretches_add_up_and_overlaps_count_once() -> None:
    entries = [
        (0, 10 * SECOND, 10),
        (10 * SECOND, 30 * SECOND, 20),
        (25 * SECOND, 40 * SECOND, 15),  # 5 s over the one before
        (5 * SECOND, 8 * SECOND, 3),  # inside the first
    ]
    assert coverage(entries, FPS24) == Coverage(40, 40 * SECOND)


def test_a_gap_isnt_covered_and_the_next_starts_past_the_furthest() -> None:
    entries = [(0, 10 * SECOND, 10), (60 * SECOND, 70 * SECOND, 10)]
    assert coverage(entries, FPS24) == Coverage(20, 70 * SECOND)


def test_amounts_without_a_stretch_count_and_lay_end_to_end() -> None:
    # An older log (an amount only) is taken to run on from IN.
    assert coverage([(None, None, 30)], FPS24) == Coverage(30, 30 * SECOND)
    mixed = [(None, None, 30), (0, 10 * SECOND, 10)]
    assert coverage(mixed, FPS24) == Coverage(40, 30 * SECOND)
    later = [(None, None, 5), (0, 20 * SECOND, 20)]
    assert coverage(later, FPS24) == Coverage(25, 20 * SECOND)


def test_drop_frame_counts_real_seconds() -> None:
    rate = timecode.rate_for("29.97", drop_frame=True)
    found = coverage([(None, None, 10)], rate)
    assert found.seconds == 10
    assert found.end == int(10 * rate.fps)
