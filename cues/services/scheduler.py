"""The work scheduler: what to write each day, across projects.

`plan` is pure: given each cue's remaining work and the days off, it
deals the work out over the days from today, so it's tested without a
database. Work is weighted: a cue's effort is its music times a factor
for its difficulty (and a further one for revisions), and each day gets
the same effort, the least that meets every deadline, up to the daily
maximum. Long cues split across days; each part carries its share of
the cue's music.

Projects are worked one at a time (each day goes to the earliest
deadline first, at one pace for everything) or in parallel (each
project keeps its own pace to its own deadlines, and every day gives
each project its share).

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


@dataclasses.dataclass
class _Lane:
    """Work dealt into its own room each day (a project, in parallel)."""

    pending: list[Work]
    stretches: list[Stretch]
    left: dict[int, Fraction]
    # Has work with no deadline, which follows at the open-ended pace.
    undated: bool

    @classmethod
    def of(cls, queue: list[Work], stretches: list[Stretch]) -> "_Lane":
        """A lane for `queue`, paced by `stretches`."""
        left = {work.key: work.effort for work in queue}
        undated = any(work.due is None for work in queue)
        return cls(list(queue), stretches, left, undated)

    def fill(
        self,
        date: datetime.date,
        room: Fraction,
        smallest: Fraction,
        calendar: Calendar,
        parts: list[Part],
        finish: dict[int, datetime.date],
    ) -> None:
        """Deals the lane's work, in order, into `room` on `date`."""
        index = 0
        while room > 0 and index < len(self.pending):
            work = self.pending[index]
            if not calendar.works(work.project, date):
                index += 1
                continue
            remaining = self.left[work.key]
            take = _take(remaining, room, smallest)
            if take is None:
                return  # too little room for a part: the day is full
            parts.append(_part(work, take))
            room -= take
            self.left[work.key] = remaining - take
            if self.left[work.key]:
                return  # split: the rest goes on the next day
            finish[work.key] = date
            self.pending.pop(index)


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
    lane = _Lane.of(queue, stretches)
    last_pin = max(pins, default=calendar.today)
    finish: dict[int, datetime.date] = {}
    days: list[Day] = []
    date = calendar.today
    for _ in range(HORIZON_DAYS):
        if not lane.pending and date > last_pin:
            break
        parts = [_part(w, w.effort, pinned=True) for w in pins.get(date, [])]
        for work in pins.get(date, []):
            finish[work.key] = date
        load = _load_on(stretches, date)
        smallest = min(Fraction(min_part), Fraction(load, 4))
        room = load - sum((p.effort for p in parts), Fraction(0))
        lane.fill(date, room, smallest, calendar, parts, finish)
        if parts:
            days.append(Day(date, tuple(parts)))
        date += _DAY
    return _Dealt(days, finish, [work.key for work in lane.pending])


def _share(
    loads: Mapping[int, Fraction], daily_max: int | None
) -> dict[int, Fraction]:
    """Each project's room in a day capped at `daily_max`.

    Everyone gets their whole load if the loads fit. If not, the cap is
    shared fairly: a project asking for less than an even share gets
    all of it, and what's left is split evenly among the rest, so the
    cap comes out of the projects that ask the most.
    """
    if daily_max is None or sum(loads.values(), Fraction(0)) <= daily_max:
        return dict(loads)
    shares: dict[int, Fraction] = {}
    left = Fraction(daily_max)
    asking = sorted(loads, key=lambda project: loads[project])
    for index, project in enumerate(asking):
        even = left / (len(asking) - index)
        shares[project] = min(loads[project], even)
        left -= shares[project]
    return {project: shares[project] for project in loads}


def _deal_parallel(
    lanes: Mapping[int, _Lane],
    pins: Mapping[datetime.date, list[Work]],
    calendar: Calendar,
    daily_max: int | None,
    min_part: int,
) -> _Dealt:
    """Gives every project its own load each day, side by side.

    A project's load is its own pace on the days it works. When the
    loads add up to more than the daily maximum, the maximum is shared
    out (`_share`). Pinned cues sit whole on their day and come out of
    their project's room.
    """
    last_pin = max(pins, default=calendar.today)
    finish: dict[int, datetime.date] = {}
    days: list[Day] = []
    date = calendar.today
    for _ in range(HORIZON_DAYS):
        busy = {p: lane for p, lane in lanes.items() if lane.pending}
        if not busy and date > last_pin:
            break
        pinned = pins.get(date, [])
        parts = [_part(w, w.effort, pinned=True) for w in pinned]
        for work in pinned:
            finish[work.key] = date
        loads = {
            p: Fraction(_load_on(lane.stretches, date))
            for p, lane in busy.items()
            if calendar.works(p, date)
        }
        shares = _share(loads, daily_max)
        for project, share in shares.items():
            room = share - sum(
                (w.effort for w in pinned if w.project == project),
                Fraction(0),
            )
            smallest = min(Fraction(min_part), share / 4)
            busy[project].fill(date, room, smallest, calendar, parts, finish)
        if parts:
            days.append(Day(date, tuple(parts)))
        date += _DAY
    unplaced = [w.key for lane in lanes.values() for w in lane.pending]
    return _Dealt(days, finish, unplaced)


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


def _split_pins(
    works: Iterable[Work], today: datetime.date
) -> tuple[dict[datetime.date, list[Work]], list[Work]]:
    """The cues pinned to a day from today on, and the rest in order."""
    pins: dict[datetime.date, list[Work]] = {}
    queue: list[Work] = []
    for work in _ordered(works):
        if work.pinned is not None and work.pinned >= today:
            pins.setdefault(work.pinned, []).append(work)
        else:
            queue.append(work)
    return pins, queue


def _paced(lane: _Lane, date: datetime.date) -> int:
    """A project's load on `date`.

    Its pace to its deadlines, then its open-ended pace only while it
    has work with no deadline.
    """
    for stretch in lane.stretches:
        if stretch.until is not None and date <= stretch.until:
            return stretch.load
    return lane.stretches[-1].load if lane.undated else 0


def _combined(lanes: Collection[_Lane]) -> list[Stretch]:
    """The projects' loads added up.

    One stretch to each deadline, then an open-ended one for the work
    with none.
    """
    ends = sorted(
        {
            stretch.until
            for lane in lanes
            for stretch in lane.stretches
            if stretch.until is not None
        }
    )
    combined = [
        Stretch(end, sum(_paced(lane, end) for lane in lanes)) for end in ends
    ]
    after = sum(lane.stretches[-1].load for lane in lanes if lane.undated)
    return [*combined, Stretch(None, after)]


def _plan_parallel(
    works: Collection[Work],
    calendar: Calendar,
    daily_max: int | None,
    min_part: int,
) -> Plan:
    """Each project paced to its own deadlines, all worked every day."""
    pins, _ = _split_pins(works, calendar.today)
    lanes: dict[int, _Lane] = {}
    for work in _ordered(works):
        if work.project in lanes:
            continue
        own = [w for w in works if w.project == work.project]
        own_pins, queue = _split_pins(own, calendar.today)
        stretches = _stretches(own, queue, own_pins, calendar, min_part)
        last = stretches[-1].load if stretches else 0
        open_load = last if daily_max is None or stretches else daily_max
        stretches.append(Stretch(None, open_load))
        lanes[work.project] = _Lane.of(queue, stretches)
    dealt = _deal_parallel(lanes, pins, calendar, daily_max, min_part)
    combined = _combined(list(lanes.values()))
    needed = max(
        (stretch.load for stretch in combined if stretch.until is not None),
        default=0,
    )
    if daily_max is not None:
        combined = [Stretch(s.until, min(s.load, daily_max)) for s in combined]
    return Plan(
        days=tuple(dealt.days),
        stretches=tuple(combined),
        needed=needed,
        risks=_risks(works, dealt),
        unplaced=tuple(dealt.unplaced),
    )


def plan(
    works: Collection[Work],
    calendar: Calendar,
    *,
    daily_max: int | None = None,
    min_part: int = 600,
    parallel: bool = False,
) -> Plan:
    """Deals the work out over the days from today.

    Args:
        works: Every cue's remaining work.
        calendar: Today and the days off.
        daily_max: The most weighted seconds a day, or None for no limit.
        min_part: The smallest part a cue is split into, weighted
            seconds (at most a quarter of the day).
        parallel: Work every project side by side, each at its own pace,
            rather than one at a time, earliest deadline first.

    Returns:
        The plan. Each working day gets the least even effort that meets
        the deadlines ahead, capped at `daily_max`; work with no deadline
        follows at the last pace (or at the maximum). In parallel, that
        holds for each project, and the day is their sum.
    """
    if parallel:
        return _plan_parallel(works, calendar, daily_max, min_part)
    pins, queue = _split_pins(works, calendar.today)
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
