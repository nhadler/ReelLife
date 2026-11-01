"""Long Play Time's maths (cues.services.long_play)."""

import pytest

from cues import timecode
from cues.services import long_play

R24 = timecode.rate_for("24")
DF = timecode.rate_for("29.97", drop_frame=True)
HOUR = 86_400  # frames at 24 fps


def _reel(picture_id: int, tc_in: int | None, length: int) -> long_play.Reel:
    return long_play.Reel(
        picture_id, tc_in, None if tc_in is None else tc_in + length
    )


def test_start_is_the_hour_then_the_leader() -> None:
    assert long_play.start(long_play.Options(1, 192), R24) == HOUR + 192
    assert long_play.start(long_play.Options(0, 0), R24) == 0
    # 01:00:00;00 at 29.97 DF is 107,892 real frames, not 108,000.
    assert long_play.start(long_play.Options(1, 0), DF) == 107_892


def test_reels_follow_each_other_by_their_length() -> None:
    reels = [
        _reel(1, HOUR, 20 * 60 * 24),  # 20 minutes from 1:00:00:00
        _reel(2, 2 * HOUR, 18 * 60 * 24),
        _reel(3, 3 * HOUR + 100, 60),
    ]
    shifts = long_play.shifts(reels, HOUR + 192)
    # A cue 10 s into reel 2 lands 20 min + 10 s after the leader.
    position = 2 * HOUR + 240 + shifts[2]
    assert position == HOUR + 192 + 20 * 60 * 24 + 240
    assert shifts == {
        1: 192,
        2: HOUR + 192 + 20 * 60 * 24 - 2 * HOUR,
        3: HOUR + 192 + 38 * 60 * 24 - (3 * HOUR + 100),
    }


def test_a_reel_without_in_and_out_takes_no_time() -> None:
    reels = [_reel(1, HOUR, 1_000), _reel(2, None, 0), _reel(3, HOUR, 500)]
    shifts = long_play.shifts(reels, 0)
    assert shifts == {1: -HOUR, 3: 1_000 - HOUR}
    assert 2 not in shifts


def test_place() -> None:
    assert long_play.place(HOUR + 10, 5, R24) == HOUR + 15
    assert long_play.place(None, 5, R24) is None
    assert long_play.place(HOUR, None, R24) is None
    # Before 00:00:00:00 or past 99:59:59:23: no place on the timeline.
    assert long_play.place(10, -11, R24) is None
    assert long_play.place(10, -10, R24) == 0
    last = timecode.max_frames(R24)
    assert long_play.place(last, 0, R24) == last
    assert long_play.place(last, 1, R24) is None


@pytest.mark.parametrize(
    ("hour", "leader", "frames"),
    [
        (1, "0:00:08:00", 192),
        (0, "00:00:08:12", 204),
        (1, "0:08", 192),
        (0, "  ", 0),
        (1, "", 0),
    ],
)
def test_options_read_the_leader(hour: int, leader: str, frames: int) -> None:
    assert long_play.options(hour, leader, R24) == long_play.Options(
        hour, frames
    )


def test_options_refuse_other_hours_and_bad_leaders() -> None:
    with pytest.raises(ValueError, match="hour 0 or 1"):
        long_play.options(2, "", R24)
    with pytest.raises(ValueError, match="leader as a timecode"):
        long_play.options(1, "eight seconds", R24)
