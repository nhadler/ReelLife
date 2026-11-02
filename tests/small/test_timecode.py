"""Timecode examples and property tests for cues.timecode."""

import fractions

import hypothesis
from hypothesis import strategies as st
import pytest

from cues import timecode
from cues.timecode import RATE_23_976
from cues.timecode import RATE_24
from cues.timecode import RATE_25
from cues.timecode import RATE_29_97
from cues.timecode import RATE_29_97_DF
from cues.timecode import RATE_59_94_DF

ALL_RATES = [
    *timecode.RATES.values(),
    RATE_29_97_DF,
    RATE_59_94_DF,
]

# Timecode examples across frame rates and input formats.


@pytest.mark.parametrize(
    ("text", "rate", "frames"),
    [
        ("1:00:10:00", RATE_23_976, 86_640),
        ("1:01:35:00", RATE_23_976, 88_680),
        ("1:03:00:00", RATE_23_976, 90_720),
        ("1:05:12:08", RATE_23_976, 93_896),
        ("01:00:00:00", RATE_25, 90_000),
        ("00:01:00;02", RATE_29_97_DF, 1_800),
        ("00:10:00;00", RATE_29_97_DF, 17_982),
        ("01:00:00;00", RATE_29_97_DF, 107_892),
    ],
)
def test_parse_plan_table(text: str, rate: timecode.Rate, frames: int) -> None:
    assert timecode.parse(text, rate) == frames


def test_format_without_leading_zero_by_default() -> None:
    assert timecode.format_timecode(86_640, RATE_23_976) == "1:00:10:00"
    assert timecode.format_timecode(88_680, RATE_23_976) == "1:01:35:00"


def test_format_drop_frame_uses_semicolon() -> None:
    text = timecode.format_timecode(1_800, RATE_29_97_DF, leading_zero=True)
    assert text == "00:01:00;02"


def test_format_duration_rounds_to_seconds() -> None:
    assert timecode.format_duration(93_896 - 90_720, RATE_23_976) == "2:12"
    assert timecode.format_duration(88_680 - 86_640, RATE_23_976) == "1:25"


def test_format_duration_rounds_half_up() -> None:
    # 80 s + 12 f at 24 fps (80.5 s) shows as 1:21.
    assert timecode.format_duration(80 * 24 + 12, RATE_24) == "1:21"
    assert timecode.format_duration(80 * 24 + 11, RATE_24) == "1:20"


def test_duration_edit_value_is_full_timecode() -> None:
    text = timecode.format_timecode(3_176, RATE_23_976, leading_zero=True)
    assert text == "00:02:12:08"


def test_format_duration_past_an_hour() -> None:
    frames = (3600 + 2 * 60 + 5) * 24
    assert timecode.format_duration(frames, RATE_24) == "1:02:05"


def test_format_duration_rejects_negative() -> None:
    with pytest.raises(ValueError, match="negative"):
        timecode.format_duration(-1, RATE_24)


def test_drop_frame_duration_tracks_wall_clock() -> None:
    # Ten DF minutes are 17,982 frames: 599.9994 s of real time, where
    # counting at the nominal 30 would give 599.4 s.
    seconds = timecode.seconds(17_982, RATE_29_97_DF)
    assert seconds == fractions.Fraction(17_982 * 1_001, 30_000)
    assert timecode.format_duration(17_982, RATE_29_97_DF) == "10:00"


def test_non_drop_seconds_count_timecode_seconds() -> None:
    assert timecode.seconds(1_440, RATE_23_976) == 60


@pytest.mark.parametrize(
    ("text", "rate", "message"),
    [
        ("00:01:00;00", RATE_29_97_DF, "skipped by drop-frame"),
        ("00:01:00;01", RATE_29_97_DF, "skipped by drop-frame"),
        ("00:01:00;03", RATE_59_94_DF, "skipped by drop-frame"),
        ("00:00:59:24", RATE_24, "frames must be < 24"),
        ("00:60:00:00", RATE_24, "must be < 60"),
        ("00:00:60:00", RATE_24, "must be < 60"),
        ("01:00:00;00", RATE_29_97, "non-drop"),
        ("1:00:10", RATE_24, "not a timecode"),
        ("100:00:00:00", RATE_24, "not a timecode"),
        ("", RATE_24, "not a timecode"),
        ("01:00:00:00.100", RATE_24, "not a timecode"),
    ],
)
def test_parse_rejects_invalid(
    text: str, rate: timecode.Rate, message: str
) -> None:
    with pytest.raises(timecode.TimecodeError, match=message):
        timecode.parse(text, rate)


def test_tenth_minute_keeps_dropped_labels() -> None:
    assert timecode.parse("00:20:00;00", RATE_29_97_DF) == 2 * 17_982


@pytest.mark.parametrize(
    "text",
    [
        "  01:00:10:00  ",
        "01:00:10:00",
        "1:00:10:00",
        "01:00:10.00",
        "01:00:10:00.42",
    ],
)
def test_parse_accepts_variants(text: str) -> None:
    assert timecode.parse(text, RATE_23_976) == 86_640


def test_parse_accepts_colons_at_drop_frame_rate() -> None:
    assert timecode.parse("00:01:00:02", RATE_29_97_DF) == 1_800


def test_parse_allows_hours_past_23() -> None:
    assert timecode.parse("25:00:00:00", RATE_24) == 25 * 3600 * 24


def test_format_subframes_suffix() -> None:
    assert (
        timecode.format_timecode(0, RATE_24, subframes=True) == "0:00:00:00.00"
    )


@pytest.mark.parametrize("frames", [-1, 99 * 3600 * 24 + 3600 * 24])
def test_format_rejects_out_of_range(frames: int) -> None:
    with pytest.raises(ValueError, match="outside"):
        timecode.format_timecode(frames, RATE_24)


def test_max_frames_is_last_label() -> None:
    last = timecode.max_frames(RATE_29_97_DF)
    assert timecode.format_timecode(last, RATE_29_97_DF) == "99:59:59;29"


@pytest.mark.parametrize(
    ("text", "rate", "frames"),
    [
        ("1:25", RATE_23_976, 85 * 24),
        ("0:00", RATE_24, 0),
        ("00:02:12:08", RATE_23_976, 3_176),
        ("1:00", RATE_29_97_DF, 1_798),
    ],
)
def test_parse_duration(text: str, rate: timecode.Rate, frames: int) -> None:
    assert timecode.parse_duration(text, rate) == frames


def test_parse_duration_rejects_bad_seconds() -> None:
    with pytest.raises(timecode.TimecodeError, match="seconds"):
        timecode.parse_duration("1:60", RATE_24)


@pytest.mark.parametrize(
    ("frames", "rate", "hour"),
    [
        (86_400, RATE_24, 1),
        (86_399, RATE_24, 0),
        (107_892, timecode.rate_for("29.97", drop_frame=True), 1),
        (107_891, timecode.rate_for("29.97", drop_frame=True), 0),
        (215_784, timecode.rate_for("59.94", drop_frame=True), 1),
    ],
)
def test_hours_follow_the_label(
    frames: int, rate: timecode.Rate, hour: int
) -> None:
    assert timecode.hours(frames, rate) == hour


def test_format_offset_is_signed() -> None:
    assert timecode.format_offset(60, RATE_24) == "+00:00:02:12"
    assert timecode.format_offset(-60, RATE_24) == "-00:00:02:12"
    assert timecode.format_offset(0, RATE_24) == "+00:00:00:00"


def test_rate_for_known_labels() -> None:
    assert timecode.rate_for("23.976") is RATE_23_976
    assert timecode.rate_for("29.97", drop_frame=True) == RATE_29_97_DF


def test_rate_for_unknown_label() -> None:
    with pytest.raises(ValueError, match="unknown frame rate"):
        timecode.rate_for("48")


@pytest.mark.parametrize("label", ["23.976", "24", "25", "30", "60"])
def test_rate_for_refuses_drop_frame_at_rates_without_it(label: str) -> None:
    with pytest.raises(ValueError, match="drop-frame is not defined"):
        timecode.rate_for(label, drop_frame=True)


def test_rate_labels() -> None:
    assert list(timecode.RATES) == [
        "23.976",
        "24",
        "25",
        "29.97",
        "30",
        "50",
        "59.94",
        "60",
    ]
    assert RATE_59_94_DF.label == "59.94 DF"
    assert RATE_23_976.fps == fractions.Fraction(24_000, 1_001)


# Property tests.


@st.composite
def _labels(draw: st.DrawFn) -> tuple[str, timecode.Rate]:
    rate = draw(st.sampled_from(ALL_RATES))
    h = draw(st.integers(0, 99))
    m = draw(st.integers(0, 59))
    s = draw(st.integers(0, 59))
    low = rate.dropped_per_minute if s == 0 and m % 10 else 0
    f = draw(st.integers(low, rate.nominal - 1))
    sep = ";" if rate.drop_frame else ":"
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{f:02d}", rate


@hypothesis.given(_labels())
def test_format_parse_round_trip(label: tuple[str, timecode.Rate]) -> None:
    text, rate = label
    frames = timecode.parse(text, rate)
    assert timecode.format_timecode(frames, rate, leading_zero=True) == text


@st.composite
def _frame_counts(draw: st.DrawFn) -> tuple[int, timecode.Rate]:
    rate = draw(st.sampled_from(ALL_RATES))
    return draw(st.integers(0, timecode.max_frames(rate))), rate


@hypothesis.given(_frame_counts())
def test_parse_format_round_trip(count: tuple[int, timecode.Rate]) -> None:
    frames, rate = count
    assert timecode.parse(timecode.format_timecode(frames, rate), rate) == (
        frames
    )


@hypothesis.given(_frame_counts())
def test_consecutive_frames_have_increasing_labels(
    count: tuple[int, timecode.Rate],
) -> None:
    frames, rate = count
    hypothesis.assume(frames < timecode.max_frames(rate))
    this = timecode.format_timecode(frames, rate, leading_zero=True)
    after = timecode.format_timecode(frames + 1, rate, leading_zero=True)
    assert this < after


@hypothesis.given(st.integers(-(10**6), 10**6))
def test_offset_round_trip(frames: int) -> None:
    text = timecode.format_offset(frames, RATE_24)
    assert timecode.parse_shift(text, RATE_24) == frames


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("48", 48),
        ("+48", 48),
        ("2.5", 2 * 24 + 5),
        ("-1.10.12", -((60 + 10) * 24 + 12)),
        ("1:01:01:01", (3600 + 60 + 1) * 24 + 1),
        ("90.0", 90 * 24),
        (" - 12 ", -12),
    ],
)
def test_parse_shift(text: str, expected: int) -> None:
    assert timecode.parse_shift(text, RATE_23_976) == expected


def test_parse_shift_drop_frame_counts_real_time() -> None:
    # One minute of real time at 29.97 is 1798.2 frames, rounded.
    assert timecode.parse_shift("1.0.0", RATE_29_97_DF) == 1798


@pytest.mark.parametrize(
    "text", ["", "abc", "1.2.3.4.5", "2.24", "1.60.0", "+-3"]
)
def test_parse_shift_refuses(text: str) -> None:
    with pytest.raises(timecode.TimecodeError):
        timecode.parse_shift(text, RATE_23_976)
