"""The work scheduler's maths."""

import datetime

from cues.services import scheduler
from cues.services.scheduler import Calendar
from cues.services.scheduler import Fraction
from cues.services.scheduler import Work

MON = datetime.date(2026, 11, 2)


def _day(offset: int) -> datetime.date:
    return MON + datetime.timedelta(days=offset)


def _work(key: int, music: int, factor: int = 1, **kwargs: object) -> Work:
    return Work(
        key=key,
        project=kwargs.pop("project", 1),  # type: ignore[arg-type]
        music=Fraction(music),
        factor=Fraction(factor),
        **kwargs,  # type: ignore[arg-type]
    )


def _shape(plan: scheduler.Plan) -> list[tuple[int, list[tuple[int, int]]]]:
    """Each day as (offset from Monday, [(cue, weighted seconds)])."""
    return [
        (
            (day.date - MON).days,
            [(part.key, int(part.effort)) for part in day.parts],
        )
        for day in plan.days
    ]


def test_the_work_is_spread_evenly_to_the_deadline() -> None:
    # 20:00 Easy plus 20:00 Hard (60:00 weighted) over four days.
    plan = scheduler.plan(
        [
            _work(1, 1200, due=_day(3)),
            _work(2, 1200, factor=3, due=_day(3)),
        ],
        Calendar(MON),
    )
    assert plan.needed == plan.load_on(MON) == 1200
    assert _shape(plan) == [
        (0, [(1, 1200)]),
        (1, [(2, 1200)]),
        (2, [(2, 1200)]),
        (3, [(2, 1200)]),
    ]
    # Each day of the Hard cue writes a third of its music.
    split = plan.days[1].parts[0]
    assert (split.music, split.of_music) == (Fraction(400), Fraction(1200))
    assert not split.whole
    assert plan.days[0].parts[0].whole
    assert plan.days[0].music == 1200
    assert plan.days[1].music == 400
    assert plan.days[1].effort == 1200
    assert not plan.risks
    assert not plan.unplaced


def test_a_split_never_leaves_a_sliver() -> None:
    plan = scheduler.plan(
        [_work(1, 700), _work(2, 1500)],
        Calendar(MON),
        daily_max=1000,
        min_part=600,
    )
    # Parts are at least 250, a quarter of the day: Monday's 300 is
    # enough to start 1m02, and Tuesday leaves it 250 for Wednesday.
    assert _shape(plan) == [
        (0, [(1, 700), (2, 300)]),
        (1, [(2, 950)]),
        (2, [(2, 250)]),
    ]


def test_too_little_room_for_a_part_ends_the_day() -> None:
    plan = scheduler.plan(
        [_work(1, 900), _work(2, 1500)],
        Calendar(MON),
        daily_max=1000,
        min_part=200,
    )
    assert _shape(plan) == [
        (0, [(1, 900)]),  # 100 is under a part
        (1, [(2, 1000)]),
        (2, [(2, 500)]),
    ]


def test_a_small_cue_finishes_in_leftover_room() -> None:
    plan = scheduler.plan(
        [_work(1, 900), _work(2, 50)],
        Calendar(MON),
        daily_max=1000,
        min_part=600,
    )
    assert _shape(plan) == [(0, [(1, 900), (2, 50)])]


def test_earlier_deadlines_go_first_and_set_the_pace() -> None:
    plan = scheduler.plan(
        [
            _work(1, 1000, due=_day(9), project=2),
            _work(2, 2000, due=_day(1)),
        ],
        Calendar(MON),
    )
    # Two days at 1000 make Tuesday; then 1m01 has eight days to spare.
    assert [(s.until, s.load) for s in plan.stretches[:2]] == [
        (_day(1), 1000),
        (_day(9), 125),
    ]
    assert plan.needed == 1000
    assert _shape(plan) == [
        (0, [(2, 1000)]),
        (1, [(2, 1000)]),
        *[(day, [(1, 125)]) for day in range(2, 10)],
    ]


def test_work_in_hand_goes_before_new_work_due_the_same_day() -> None:
    plan = scheduler.plan(
        [
            _work(1, 600, due=_day(1), rank=(0,)),
            _work(2, 600, due=_day(1), rank=(1,), started=True),
        ],
        Calendar(MON),
    )
    assert _shape(plan) == [(0, [(2, 600)]), (1, [(1, 600)])]


def test_days_off_are_skipped() -> None:
    calendar = Calendar(MON, shared_off=frozenset({_day(0), _day(2)}))
    plan = scheduler.plan([_work(1, 1200, due=_day(3))], calendar)
    assert calendar.works(1, _day(1))
    assert not calendar.works(1, _day(2))
    assert plan.load_on(MON) == 600  # two working days: Tue and Thu
    assert _shape(plan) == [(1, [(1, 600)]), (3, [(1, 600)])]


def test_a_projects_own_day_off_stops_only_its_work() -> None:
    calendar = Calendar(MON, project_off={1: frozenset({_day(0)})})
    plan = scheduler.plan(
        [
            _work(1, 600, due=_day(1), project=1),
            _work(2, 600, due=_day(5), project=2),
        ],
        calendar,
        daily_max=600,
    )
    assert _shape(plan) == [(0, [(2, 600)]), (1, [(1, 600)])]


def test_a_pinned_cue_sits_whole_on_its_day() -> None:
    plan = scheduler.plan(
        [
            _work(1, 1500, due=_day(1)),
            _work(2, 900, pinned=_day(1), due=_day(5)),
            _work(3, 300, pinned=_day(-1)),  # past: back in the flow
        ],
        Calendar(MON),
        daily_max=1200,
        min_part=300,
    )
    tuesday = plan.day(_day(1))
    assert tuesday is not None
    assert [(p.key, int(p.effort), p.pinned) for p in tuesday.parts] == [
        (2, 900, True),
        (1, 300, False),
    ]
    assert plan.day(_day(4)) is None
    assert 3 in [p.key for day in plan.days for p in day.parts]


def test_a_daily_maximum_below_the_pace_names_the_missed_deadline() -> None:
    plan = scheduler.plan(
        [
            _work(1, 1200, due=_day(1), project=1),
            _work(2, 600, due=_day(1), project=1),
        ],
        Calendar(MON),
        daily_max=600,
        min_part=1,
    )
    assert (plan.needed, plan.load_on(MON)) == (900, 600)
    assert plan.risks == (scheduler.Risk(1, _day(1), _day(2)),)


def test_work_without_a_deadline_follows_at_the_maximum() -> None:
    plan = scheduler.plan([_work(1, 1500)], Calendar(MON), daily_max=600)
    assert plan.needed == 0
    assert _shape(plan) == [
        (0, [(1, 600)]),
        (1, [(1, 600)]),
        (2, [(1, 300)]),
    ]


def test_without_a_deadline_or_maximum_nothing_is_planned() -> None:
    plan = scheduler.plan([_work(1, 900)], Calendar(MON))
    assert (plan.load_on(MON), plan.days, plan.unplaced) == (0, (), (1,))


def test_a_deadline_on_the_projects_day_off_cant_be_met() -> None:
    calendar = Calendar(MON, project_off={1: frozenset({_day(0)})})
    plan = scheduler.plan([_work(1, 600, due=_day(0))], calendar)
    assert plan.needed == 600
    assert plan.risks == (scheduler.Risk(1, _day(0), _day(1)),)


def test_work_past_the_horizon_is_left_unplaced() -> None:
    plan = scheduler.plan(
        [_work(1, 10**9, due=_day(1)), _work(2, 10**9, due=_day(1))],
        Calendar(MON),
        daily_max=1,
        min_part=1,
    )
    assert plan.unplaced == (1, 2)
    assert plan.risks == (scheduler.Risk(1, _day(1), None),)


def test_nothing_to_do() -> None:
    plan = scheduler.plan([], Calendar(MON))
    assert (plan.days, plan.load_on(MON), plan.needed) == ((), 0, 0)
