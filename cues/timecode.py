"""SMPTE timecode parsing, formatting and arithmetic.

Timecodes are stored as integer frame counts at a project's nominal rate.
At 23.976 a timecode second holds 24 frames, so
1:00:10:00 is 86,640 frames. Drop-frame rates count real
frames and skip labels: frames 00 and 01 at the start of every minute
except each tenth minute (00 to 03 at 59.94 DF).

This module has no Django imports so it can be tested and reused alone.
"""

import dataclasses
import fractions
import re

_MAX_HOURS = 99
_SECONDS_PER_MINUTE = 60
_MINUTES_PER_HOUR = 60

_TIMECODE_RE = re.compile(
    r"""
    ^\s*
    (?P<h>\d{1,2}) : (?P<m>\d{2}) : (?P<s>\d{2})
    (?P<sep>[:;.]) (?P<f>\d{2})
    (?: \. (?P<sub>\d{2}) )?
    \s*$
    """,
    re.VERBOSE,
)
_MINUTES_SECONDS_RE = re.compile(r"^\s*(?P<m>\d{1,3}):(?P<s>\d{2})\s*$")


class TimecodeError(ValueError):
    """Raised when text is not a valid timecode at the given rate."""


@dataclasses.dataclass(frozen=True)
class Rate:
    """A frame rate.

    Attributes:
        fps: True frames per second, e.g. 24000/1001 for 23.976.
        nominal: Frames per timecode second, e.g. 24 for 23.976.
        drop_frame: Whether timecode labels skip frame numbers to track
            wall-clock time. Only valid for 29.97 and 59.94.
    """

    fps: fractions.Fraction
    nominal: int
    drop_frame: bool = False

    def __post_init__(self) -> None:
        """Rejects drop-frame on rates that have no drop-frame form.

        Raises:
            ValueError: If drop_frame is set on a rate other than 29.97 or
                59.94.
        """
        if self.drop_frame and (
            self.nominal not in (30, 60) or self.fps.denominator == 1
        ):
            raise ValueError(f"drop-frame is not defined at {self.label}")

    @property
    def label(self) -> str:
        """The rate as composers write it, e.g. '23.976' or '29.97 DF'."""
        if self.fps.denominator == 1:
            text = str(self.fps.numerator)
        else:
            text = f"{float(self.fps):.3f}".rstrip("0")
        return f"{text} DF" if self.drop_frame else text

    @property
    def dropped_per_minute(self) -> int:
        """Labels skipped at the start of each non-tenth minute."""
        return self.nominal // 15 if self.drop_frame else 0


def _ntsc(nominal: int) -> fractions.Fraction:
    return fractions.Fraction(nominal * 1000, 1001)


RATE_23_976 = Rate(_ntsc(24), 24)
RATE_24 = Rate(fractions.Fraction(24), 24)
RATE_25 = Rate(fractions.Fraction(25), 25)
RATE_29_97 = Rate(_ntsc(30), 30)
RATE_29_97_DF = Rate(_ntsc(30), 30, drop_frame=True)
RATE_30 = Rate(fractions.Fraction(30), 30)
RATE_50 = Rate(fractions.Fraction(50), 50)
RATE_59_94 = Rate(_ntsc(60), 60)
RATE_59_94_DF = Rate(_ntsc(60), 60, drop_frame=True)
RATE_60 = Rate(fractions.Fraction(60), 60)

# Project frame rate choices, keyed by the label stored on a project.
RATES = {
    rate.label: rate
    for rate in (
        RATE_23_976,
        RATE_24,
        RATE_25,
        RATE_29_97,
        RATE_30,
        RATE_50,
        RATE_59_94,
        RATE_60,
    )
}


def rate_for(label: str, *, drop_frame: bool = False) -> Rate:
    """Looks up a project frame rate.

    Args:
        label: One of the keys of RATES, e.g. '23.976'.
        drop_frame: Whether the project uses drop-frame timecode.

    Returns:
        The matching Rate.

    Raises:
        ValueError: If the label is unknown, or drop_frame is set on a rate
            without a drop-frame form.
    """
    try:
        rate = RATES[label]
    except KeyError:
        raise ValueError(f"unknown frame rate {label!r}") from None
    if drop_frame:
        return dataclasses.replace(rate, drop_frame=True)
    return rate


def _frames_per_minute(rate: Rate) -> int:
    return rate.nominal * _SECONDS_PER_MINUTE


def _label_to_frames(h: int, m: int, s: int, f: int, rate: Rate) -> int:
    total_minutes = h * _MINUTES_PER_HOUR + m
    label_frames = (total_minutes * _SECONDS_PER_MINUTE + s) * rate.nominal
    dropped = rate.dropped_per_minute * (total_minutes - total_minutes // 10)
    return label_frames + f - dropped


def _frames_to_label(frames: int, rate: Rate) -> tuple[int, int, int, int]:
    drop = rate.dropped_per_minute
    if drop:
        per_minute = _frames_per_minute(rate) - drop
        per_ten_minutes = 10 * _frames_per_minute(rate) - 9 * drop
        tens, rest = divmod(frames, per_ten_minutes)
        frames += 9 * drop * tens
        if rest > drop:
            frames += drop * ((rest - drop) // per_minute)
    total_seconds, f = divmod(frames, rate.nominal)
    total_minutes, s = divmod(total_seconds, _SECONDS_PER_MINUTE)
    h, m = divmod(total_minutes, _MINUTES_PER_HOUR)
    return h, m, s, f


def hours(frames: int, rate: Rate) -> int:
    """The hours part of a frame count's label (01 in 01:00:00;00).

    Drop-frame labels skip numbers, so dividing by frames per hour is
    wrong near each hour's start.
    """
    return _frames_to_label(frames, rate)[0]


def max_frames(rate: Rate) -> int:
    """Returns the frame count of the last label, 99:59:59:FF."""
    last = _MAX_HOURS, 59, 59, rate.nominal - 1
    return _label_to_frames(*last, rate)


def parse(text: str, rate: Rate) -> int:
    """Parses an absolute timecode into a frame count.

    Accepts H:MM:SS:FF and HH:MM:SS:FF, with ';' or '.' before the frames
    and an optional '.ss' subframe suffix (ignored). Surrounding whitespace
    is ignored, since Pro Tools pads timecodes with spaces.

    Args:
        text: The timecode text.
        rate: The project's frame rate.

    Returns:
        Frames since 00:00:00:00.

    Raises:
        TimecodeError: If the text is malformed, a field is out of range,
            ';' is used at a non-drop rate, or the label is one that
            drop-frame skips.
    """
    match = _TIMECODE_RE.match(text)
    if match is None:
        raise TimecodeError(f"not a timecode: {text!r}")
    h, m, s, f = (int(match[name]) for name in "hmsf")
    if m >= _MINUTES_PER_HOUR or s >= _SECONDS_PER_MINUTE:
        raise TimecodeError(f"minutes and seconds must be < 60: {text!r}")
    if f >= rate.nominal:
        raise TimecodeError(
            f"frames must be < {rate.nominal} at {rate.label}: {text!r}"
        )
    if match["sep"] == ";" and not rate.drop_frame:
        raise TimecodeError(
            f"drop-frame timecode at non-drop rate {rate.label}: {text!r}"
        )
    if s == 0 and f < rate.dropped_per_minute and m % 10 != 0:
        raise TimecodeError(f"label skipped by drop-frame: {text!r}")
    return _label_to_frames(h, m, s, f, rate)


def format_timecode(
    frames: int,
    rate: Rate,
    *,
    leading_zero: bool = False,
    subframes: bool = False,
) -> str:
    """Formats a frame count as a timecode label.

    Args:
        frames: Frames since 00:00:00:00.
        rate: The project's frame rate.
        leading_zero: Pad hours to two digits ('01:00:10:00').
        subframes: Append '.00'. Subframes are never stored, so this only
            matches the display some DAWs expect.

    Returns:
        The label, with ';' before the frames at drop-frame rates.

    Raises:
        ValueError: If frames is negative or past 99:59:59:FF.
    """
    if not 0 <= frames <= max_frames(rate):
        raise ValueError(f"{frames} frames is outside 0 to 99:59:59:FF")
    h, m, s, f = _frames_to_label(frames, rate)
    sep = ";" if rate.drop_frame else ":"
    hh = f"{h:02d}" if leading_zero else str(h)
    text = f"{hh}:{m:02d}:{s:02d}{sep}{f:02d}"
    return f"{text}.00" if subframes else text


def seconds(frames: int, rate: Rate) -> fractions.Fraction:
    """Returns the timecode duration of a frame count, in seconds.

    Non-drop rates count timecode seconds (frames / nominal), so a cue of
    1,440 frames at 23.976 is 60 s, as cue sheets expect. Drop-frame exists
    to keep labels on wall-clock time, so there it is frames / fps, which
    agrees with the label difference.

    Args:
        frames: A frame count, typically a duration.
        rate: The project's frame rate.

    Returns:
        The exact duration in seconds.
    """
    return frames / duration_fps(rate)


def duration_fps(rate: Rate) -> fractions.Fraction:
    """Frames per second of duration, as `seconds` counts them."""
    return rate.fps if rate.drop_frame else fractions.Fraction(rate.nominal)


def _round_half_up(value: fractions.Fraction) -> int:
    return int((value + fractions.Fraction(1, 2)) // 1)


def format_duration(frames: int, rate: Rate) -> str:
    """Formats a duration for the grid and cue sheets.

    Rounds to the nearest second, halves up: 80 s + 12 f at
    24 fps shows as '1:21'.

    Args:
        frames: A non-negative frame count.
        rate: The project's frame rate.

    Returns:
        'm:ss', or 'h:mm:ss' from one hour.

    Raises:
        ValueError: If frames is negative.
    """
    if frames < 0:
        raise ValueError(f"durations cannot be negative: {frames}")
    total = _round_half_up(seconds(frames, rate))
    total_minutes, s = divmod(total, _SECONDS_PER_MINUTE)
    h, m = divmod(total_minutes, _MINUTES_PER_HOUR)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def parse_duration(text: str, rate: Rate) -> int:
    """Parses a duration typed into a DUR cell.

    Args:
        text: 'm:ss' (whole seconds) or a full 'hh:mm:ss:ff' counted from
            zero, the form format_timecode(leading_zero=True) produces.
        rate: The project's frame rate.

    Returns:
        The duration in frames.

    Raises:
        TimecodeError: If the text is neither form.
    """
    match = _MINUTES_SECONDS_RE.match(text)
    if match is None:
        return parse(text, rate)
    m, s = int(match["m"]), int(match["s"])
    if s >= _SECONDS_PER_MINUTE:
        raise TimecodeError(f"seconds must be < 60: {text!r}")
    total = m * _SECONDS_PER_MINUTE + s
    if rate.drop_frame:
        return _round_half_up(total * rate.fps)
    return total * rate.nominal


def format_offset(frames: int, rate: Rate) -> str:
    """Formats a signed frame difference, e.g. '+00:00:02:12'.

    Args:
        frames: The difference in frames; may be negative.
        rate: The project's frame rate.

    Returns:
        The sign followed by the magnitude as a leading-zero timecode.
    """
    sign = "-" if frames < 0 else "+"
    return sign + format_timecode(abs(frames), rate, leading_zero=True)


_SHIFT_RE = re.compile(
    r"^\s*(?:(?P<sign>[+-])\s*)?(?P<body>\d+(?:[.:]\d+){0,3})\s*$"
)


def parse_shift(text: str, rate: Rate) -> int:
    """Parses the shift tool's offset.

    Fields are read from the right: frames, seconds, minutes, hours,
    separated by '.' or ':'. Digits alone are frames, '2.5' is 2 s 5 fr,
    '-1.10.12' is back 1 min 10 s 12 fr and '1:01:01:01' a full
    timecode's worth. A leading '-' moves earlier, '+' is optional. Only
    the leftmost field may run past its usual range ('90.0' is 90 s).

    Args:
        text: What the user typed.
        rate: The project's frame rate.

    Returns:
        The offset in frames, negative for '-'. At drop-frame rates the
        time is real time, as for durations.

    Raises:
        TimecodeError: If the text is malformed or a field is too large.
    """
    match = _SHIFT_RE.match(text)
    if match is None:
        raise TimecodeError(f"not an offset: {text!r}")
    fields = [int(part) for part in re.split(r"[.:]", match["body"])]
    fields.reverse()  # frames, seconds, minutes, hours
    limits = (rate.nominal, _SECONDS_PER_MINUTE, _MINUTES_PER_HOUR)
    for index, value in enumerate(fields[:-1]):
        if value >= limits[index]:
            raise TimecodeError(f"a field is out of range: {text!r}")
    frames = fields[0]
    total = 0
    for value, scale in zip(fields[1:], (1, 60, 3600), strict=False):
        total += value * scale
    if rate.drop_frame:
        frames += _round_half_up(total * rate.fps)
    else:
        frames += total * rate.nominal
    return -frames if match["sign"] == "-" else frames
