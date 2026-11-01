"""Dates as people read them on a page.

Pages name days ("Fri Nov 13", "Nov 13") rather than print numeric
dates; the numeric Date Format setting is for exports and form inputs.
Each function takes month_first: Nov 13 (US) or 13 Nov (everyone else).
"""

import datetime

from django.utils import dateformat
from django.utils import timezone


def _day(day: datetime.date, month_first: bool) -> str:
    """'Nov 13' or '13 Nov'."""
    month = dateformat.format(day, "M")
    return f"{month} {day.day}" if month_first else f"{day.day} {month}"


def _with_year(day: datetime.date, month_first: bool) -> str:
    """'Nov 13, 2026' or '13 Nov 2026'."""
    sep = ", " if month_first else " "
    return f"{_day(day, month_first)}{sep}{day.year}"


def week(first: datetime.date, last: datetime.date, month_first: bool) -> str:
    """A run of days, naming the month and year once where they repeat.

    >>> week(datetime.date(2026, 11, 1), datetime.date(2026, 11, 7), True)
    'Nov 1 – 7, 2026'
    >>> week(datetime.date(2026, 10, 29), datetime.date(2026, 11, 4), False)
    '29 Oct – 4 Nov 2026'
    """
    if first.year != last.year:
        start = _with_year(first, month_first)
    elif first.month != last.month:
        start = _day(first, month_first)
    elif month_first:
        start = _day(first, month_first)
        return f"{start} – {last.day}, {last.year}"
    else:
        start = str(first.day)
    return f"{start} – {_with_year(last, month_first)}"


def stamp(
    when: datetime.datetime,
    month_first: bool,
    today: datetime.date | None = None,
) -> str:
    """When something happened, in the viewer's time zone.

    'Today 16:59', 'Yesterday 09:12', 'Oct 30, 16:59', and the year
    too once it isn't this one.
    """
    local = timezone.localtime(when)
    today = today or timezone.localdate()
    clock = f"{local:%H:%M}"
    day = local.date()
    if day == today:
        return f"Today {clock}"
    if day == today - datetime.timedelta(days=1):
        return f"Yesterday {clock}"
    if day.year == today.year:
        return f"{_day(day, month_first)}, {clock}"
    return f"{_with_year(day, month_first)}, {clock}"
