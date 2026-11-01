"""The work scheduler: what to write each day, across projects.

`plan` is pure: given each cue's remaining work and the days off, it
deals the work out over the days from today, so it's tested without a
database. Work is weighted: a cue's effort is its music times a factor
for its difficulty (and a further one for revisions), and each day gets
the same effort, the least that meets every deadline, up to the daily
maximum. Long cues split across days; each part carries its share of
the cue's music.

The plan is worked out afresh each time from where things stand, so
work finished early pulls the rest forward and work not finished yet
shifts into the coming days; nothing is ever late.
"""

from collections.abc import Collection, Iterable, Mapping
import dataclasses
import datetime
import fractions
import math
from typing import Any

Fraction = fractions.Fraction
_DAY = datetime.timedelta(days=1)
# How far ahead to plan before giving up on the rest (three years).
HORIZON_DAYS = 3 * 365


@dataclasses.dataclass(frozen=True)
class Work:
    """One cue's remaining work.

    Attributes:
        key: The cue's id.
        project: Its project's id, for the project's own days off.
        music: Music still to write, in seconds.
        factor: Its difficulty factor (times the revise factor for
            revisions): effort is music times this.
        due: The deadline it counts toward; None plans it after all work
            that has one.
        rank: Order among work due the same day (priority, project,
            picture order, IN).
        started: In progress: goes before unstarted work due the same
            day, so work in hand isn't pushed back.
        pinned: The day the composer fixed it to, if today or later.
    """

    key: int
    project: int
    music: Fraction
    factor: Fraction
    due: datetime.date | None = None
    rank: tuple[Any, ...] = ()
    started: bool = False
    pinned: datetime.date | None = None

    @property
    def effort(self) -> Fraction:
        """Weighted seconds: music times the factor."""
        return self.music * self.factor


@dataclasses.dataclass(frozen=True)
class Part:
    """A cue, or the share of it, written on one day.

    Attributes:
        key: The cue's id.
        effort: Weighted seconds.
        music: That day's share of the cue's music, in seconds.
        of_music: All the music the cue has left, in seconds.
        pinned: Fixed to this day by the composer.
    """

    key: int
    effort: Fraction
    music: Fraction
    of_music: Fraction
    pinned: bool = False

    @property
    def whole(self) -> bool:
        """The cue fits in this day."""
        return self.music == self.of_music


@dataclasses.dataclass(frozen=True)
class Day:
    """One working day's plan."""

    date: datetime.date
    parts: tuple[Part, ...]

    @property
    def effort(self) -> Fraction:
        """Weighted seconds planned."""
        return sum((part.effort for part in self.parts), Fraction(0))

    @property
    def music(self) -> Fraction:
        """Music planned, in seconds."""
        return sum((part.music for part in self.parts), Fraction(0))


@dataclasses.dataclass(frozen=True)
class Risk:
    """A deadline the plan misses at the daily maximum.

    Attributes:
        project: The project's id.
        due: The deadline.
        finish: The day its last cue would be done, or None if past the
            planning horizon.
    """

    project: int
    due: datetime.date
    finish: datetime.date | None


@dataclasses.dataclass(frozen=True)
class Calendar:
    """Which days are worked.

    Attributes:
        today: The first day planned.
        shared_off: The composer's days off, for every project.
        project_off: Each project's own days off (its studio is closed):
            only that project's work stops.
    """

    today: datetime.date
    shared_off: frozenset[datetime.date] = frozenset()
    project_off: Mapping[int, frozenset[datetime.date]] = dataclasses.field(
        default_factory=dict
    )

    def works(self, project: int, day: datetime.date) -> bool:
        """Whether `project`'s work can be done on `day`."""
        return day not in self.shared_off and day not in self.project_off.get(
            project, frozenset()
        )


@dataclasses.dataclass(frozen=True)
class Stretch:
    """A run of days at one load.

    Attributes:
        until: The stretch's last day (a deadline), or None for every day
            after the stretches before it.
        load: Weighted seconds dealt to each working day.
    """

    until: datetime.date | None
    load: int


def _load_on(stretches: Iterable[Stretch], date: datetime.date) -> int:
    """The load on `date`; the last stretch is always open-ended."""
    return next(s.load for s in stretches if s.until is None or date <= s.until)


@dataclasses.dataclass(frozen=True)
class Plan:
    """The work dealt out over the days.

    Attributes:
        days: Days with work, in order.
        stretches: The load day by day: each runs to the deadline that
            sets its pace, then the next, easier one takes over.
        needed: The most weighted seconds a day it takes to meet every
            deadline (0 when nothing has one).
        risks: Deadlines missed because the daily maximum is below what
            they need.
        unplaced: Cues that didn't fit before the horizon (or at all,
            with no deadline and no daily maximum), in order.
    """

    days: tuple[Day, ...]
    stretches: tuple[Stretch, ...]
    needed: int
    risks: tuple[Risk, ...] = ()
    unplaced: tuple[int, ...] = ()

    def load_on(self, date: datetime.date) -> int:
        """Weighted seconds planned for each working day like `date`."""
        return _load_on(self.stretches, date)

    def day(self, date: datetime.date) -> Day | None:
        """The plan for `date`, if it has work."""
        for day in self.days:
            if day.date == date:
                return day
        return None


def _ordered(works: Iterable[Work]) -> list[Work]:
    return sorted(
        works,
        key=lambda w: (
            w.due is None,
            w.due or datetime.date.max,
            not w.started,
            w.rank,
            w.key,
        ),
    )


def _part(work: Work, effort: Fraction, pinned: bool = False) -> Part:
    return Part(
        key=work.key,
        effort=effort,
        music=effort / work.factor,
        of_music=work.music,
        pinned=pinned,
    )


@dataclasses.dataclass
class _Dealt:
    days: list[Day]
    finish: dict[int, datetime.date]
    unplaced: list[int]


def _take(
    remaining: Fraction, room: Fraction, smallest: Fraction
) -> Fraction | None:
    """How much of a cue to write in the day's room; None for tomorrow.

    A cue that fits is finished. One that doesn't is split, leaving at
    least `smallest` for later; when that leaves less than `smallest`
    now, the day is full and the cue waits for the next one.
    """
    if remaining <= room:
        return remaining
    take = min(room, remaining - smallest)
    return take if take >= smallest else None


def _deal(
    queue: list[Work],
    pins: Mapping[datetime.date, list[Work]],
    calendar: Calendar,
    stretches: list[Stretch],
    min_part: int,
) -> _Dealt:
    """Fills each working day with its stretch's load, in order.

    No part is smaller than the minimum part or a quarter of the day,
    whichever is less, so an empty day always takes some of a cue.
    Pinned cues sit whole on their day.
    """
    left = {work.key: work.effort for work in queue}
    pending = list(queue)
    last_pin = max(pins, default=calendar.today)
    finish: dict[int, datetime.date] = {}
    days: list[Day] = []
    date = calendar.today
    for _ in range(HORIZON_DAYS):
        if not pending and date > last_pin:
            break
        parts = [_part(w, w.effort, pinned=True) for w in pins.get(date, [])]
        for work in pins.get(date, []):
            finish[work.key] = date
        load = _load_on(stretches, date)
        smallest = min(Fraction(min_part), Fraction(load, 4))
        room = load - sum((p.effort for p in parts), Fraction(0))
        index = 0
        while room > 0 and index < len(pending):
            work = pending[index]
            if not calendar.works(work.project, date):
                index += 1
                continue
            remaining = left[work.key]
            take = _take(remaining, room, smallest)
            if take is None:
                break  # too little room for a part: the day is full
            parts.append(_part(work, take))
            room -= take
            left[work.key] = remaining - take
            if left[work.key]:
                break  # split: the rest goes on the next day
            finish[work.key] = date
            pending.pop(index)
        if parts:
            days.append(Day(date, tuple(parts)))
        date += _DAY
    return _Dealt(days, finish, [work.key for work in pending])


def _missed(works: Iterable[Work], dealt: _Dealt) -> list[datetime.date]:
    """The deadlines some work finishes after, earliest first."""
    return sorted(
        {
            work.due
            for work in works
            if work.due is not None
            and dealt.finish.get(work.key, datetime.date.max) > work.due
        }
    )


def _stretches(
    works: list[Work],
    queue: list[Work],
    pins: Mapping[datetime.date, list[Work]],
    calendar: Calendar,
    min_part: int,
) -> list[Stretch]:
    """The least even load to each deadline that sets the pace.

    From today, a binary search over trial plans finds the least load
    that meets every deadline (more a day never finishes anything
    later). The earliest deadline one second less would miss is the one
    that sets that pace, so the load holds until it; from the next day
    the search runs again for the deadlines after it, which need no
    more and usually less. If even everything at once misses a deadline
    (it falls on the project's day off, say), that is the load.
    """
    # Pinned work sits on its day whatever the load, so only the rest
    # sets a pace.
    deadlines = sorted({w.due for w in queue if w.due is not None})
    high = max(1, math.ceil(sum((w.effort for w in queue), Fraction(0))))
    stretches: list[Stretch] = []
    while True:
        start = stretches[-1].until if stretches else None
        later = [d for d in deadlines if start is None or d > start]
        if not later:
            return stretches

        def trial(load: int) -> _Dealt:
            tail = [*stretches, Stretch(None, load)]
            return _deal(queue, pins, calendar, tail, min_part)

        load = high
        if not _missed(works, trial(high)):
            low = 1
            while low < load:
                middle = (low + load) // 2
                if _missed(works, trial(middle)):
                    low = middle + 1
                else:
                    load = middle
        binding = [d for d in _missed(works, trial(load - 1)) if d in later]
        stretches.append(Stretch(binding[0] if binding else later[-1], load))


def _risks(works: Iterable[Work], dealt: _Dealt) -> tuple[Risk, ...]:
    """Each (project, deadline) with work finishing after it."""
    missed: dict[tuple[int, datetime.date], list[datetime.date | None]] = {}
    for work in works:
        if work.due is None:
            continue
        done = dealt.finish.get(work.key)
        if done is None or done > work.due:
            missed.setdefault((work.project, work.due), []).append(done)
    return tuple(
        Risk(project, due, None if None in dates else max(filter(None, dates)))
        for (project, due), dates in sorted(missed.items())
    )


def plan(
    works: Collection[Work],
    calendar: Calendar,
    *,
    daily_max: int | None = None,
    min_part: int = 600,
) -> Plan:
    """Deals the work out over the days from today.

    Args:
        works: Every cue's remaining work.
        calendar: Today and the days off.
        daily_max: The most weighted seconds a day, or None for no limit.
        min_part: The smallest part a cue is split into, weighted
            seconds (at most a quarter of the day).

    Returns:
        The plan. Each working day gets the least even effort that meets
        the deadlines ahead, capped at `daily_max`; work with no deadline
        follows at the last pace (or at the maximum).
    """
    pins: dict[datetime.date, list[Work]] = {}
    queue: list[Work] = []
    for work in _ordered(works):
        if work.pinned is not None and work.pinned >= calendar.today:
            pins.setdefault(work.pinned, []).append(work)
        else:
            queue.append(work)
    stretches = _stretches(list(works), queue, pins, calendar, min_part)
    needed = max((stretch.load for stretch in stretches), default=0)
    # Work with no deadline follows at the last pace, or the maximum.
    last = stretches[-1].load if stretches else 0
    stretches.append(Stretch(None, last if daily_max is None else daily_max))
    if daily_max is not None:
        stretches = [
            Stretch(s.until, min(s.load, daily_max)) for s in stretches
        ]
    dealt = _deal(queue, pins, calendar, stretches, min_part)
    return Plan(
        days=tuple(dealt.days),
        stretches=tuple(stretches),
        needed=needed,
        risks=_risks(works, dealt),
        unplaced=tuple(dealt.unplaced),
    )
