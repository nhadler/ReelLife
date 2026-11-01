"""Dates as pages show them (cues.dates)."""

import datetime

from django.utils import timezone
import pytest

from cues import dates
from cues.templatetags import text

_D = datetime.date


@pytest.mark.parametrize(
    ("first", "last", "month_first", "expected"),
    [
        (_D(2026, 11, 1), _D(2026, 11, 7), True, "Nov 1 – 7, 2026"),
        (_D(2026, 11, 1), _D(2026, 11, 7), False, "1 – 7 Nov 2026"),
        (_D(2026, 10, 29), _D(2026, 11, 4), True, "Oct 29 – Nov 4, 2026"),
        (_D(2026, 10, 29), _D(2026, 11, 4), False, "29 Oct – 4 Nov 2026"),
        (_D(2026, 12, 27), _D(2027, 1, 2), True, "Dec 27, 2026 – Jan 2, 2027"),
        (_D(2026, 12, 27), _D(2027, 1, 2), False, "27 Dec 2026 – 2 Jan 2027"),
    ],
)
def test_week(
    first: datetime.date, last: datetime.date, month_first: bool, expected: str
) -> None:
    assert dates.week(first, last, month_first) == expected


@pytest.mark.parametrize(
    ("day", "month_first", "expected"),
    [
        (_D(2026, 11, 1), True, "Today 16:59"),
        (_D(2026, 10, 31), True, "Yesterday 16:59"),
        (_D(2026, 10, 30), True, "Oct 30, 16:59"),
        (_D(2026, 10, 30), False, "30 Oct, 16:59"),
        (_D(2025, 10, 30), True, "Oct 30, 2025, 16:59"),
        (_D(2025, 10, 30), False, "30 Oct 2025, 16:59"),
    ],
)
def test_stamp(day: datetime.date, month_first: bool, expected: str) -> None:
    when = timezone.make_aware(
        datetime.datetime.combine(day, datetime.time(16, 59))
    )
    assert dates.stamp(when, month_first, today=_D(2026, 11, 1)) == expected


def test_stamp_defaults_to_today() -> None:
    assert dates.stamp(timezone.now(), True).startswith("Today ")


def test_stamp_filter() -> None:
    assert not text.stamp(None, "M j")
    assert text.stamp(timezone.now(), "j M").startswith("Today ")
