"""The cue tools: plans, changes and undo."""

import pytest

from cues import models
from cues.services import authoring
from cues.services import history
from cues.services import tools
from tests import factories

pytestmark = pytest.mark.django_db

SECOND = 24  # frames at 23.976
HOUR = 3600 * SECOND


def _reel(project: models.Project, name: str) -> models.Picture:
    picture = factories.PictureFactory.create(project=project, name=name)
    factories.PictureVersionFactory.create(picture=picture)
    return picture


def _cue(picture: models.Picture, number: str, start: int) -> models.Cue:
    """A 30-second cue with a v1, `start` seconds into hour 1."""
    project = picture.project
    cue = factories.CueFactory.create(
        project=project,
        picture=picture,
        number=number,
        tc_in=HOUR + start * SECOND,
        tc_out=HOUR + (start + 30) * SECOND,
        status=project.statuses.get(label="To Write"),
    )
    version = factories.CueVersionFactory.create(
        cue=cue,
        label="v1",
        written_to=picture.current_version(),
        spotting_notes=f"Notes for {number}",
    )
    cue.current_version = version
    cue.save()
    return cue


def _current(cue: models.Cue) -> models.CueVersion:
    version = cue.current_version
    assert version is not None
    return version


def _status(cue: models.Cue) -> models.StatusOption:
    status = cue.status
    assert status is not None
    return status


def _times(cue: models.Cue) -> tuple[int, int]:
    assert cue.tc_in is not None and cue.tc_out is not None
    return cue.tc_in, cue.tc_out


@pytest.fixture(name="cues")
def fixture_cues() -> list[models.Cue]:
    """1m01–1m04 in reel 1, a minute apart."""
    project = factories.ProjectFactory.create()
    reel = _reel(project, "1")
    return [_cue(reel, f"1m0{i + 1}", i * 60) for i in range(4)]


def _undo_last(project: models.Project) -> None:
    history.undo(models.ChangeSet.objects.filter(project=project).latest("pk"))


def _numbers(project: models.Project) -> list[str]:
    return sorted(project.cues.values_list("number", flat=True))


def test_new_cues_number_up_and_undo(cues: list[models.Cue]) -> None:
    project = cues[0].project
    reel = cues[0].picture
    numbers = tools.plan_new_cues(reel, "1m05s", 3)
    assert numbers == ["1m05s", "1m06s", "1m07s"]
    status = project.statuses.get(label="To Write")
    made = tools.new_cues(reel, numbers, status=status)
    assert [_current(cue).label for cue in made] == ["v1"] * 3
    assert _current(made[0]).written_to == reel.current_version()
    _undo_last(project)
    assert _numbers(project) == ["1m01", "1m02", "1m03", "1m04"]


def test_new_cues_refuse_bad_numbers(cues: list[models.Cue]) -> None:
    reel = cues[0].picture
    with pytest.raises(tools.ToolError, match=r"1m03 \(in use\)"):
        tools.plan_new_cues(reel, "1m02", 3)
    with pytest.raises(tools.ToolError, match="no digits"):
        tools.plan_new_cues(reel, "Main", 2)
    reel.project.cue_number_pattern = r"^\d+m\d+$"
    with pytest.raises(tools.ToolError, match="doesn't fit Valid Cue Numbers"):
        tools.plan_new_cues(reel, "1m05s", 1)


def test_check_numbers_reports_blanks_and_repeats(
    cues: list[models.Cue],
) -> None:
    with pytest.raises(tools.ToolError, match="a blank number, 1m09 twice"):
        tools.check_renumber(cues[:3], ["", "1m09", "1m09"])


def test_add_versions_keeps_notes_and_marks_composed(
    cues: list[models.Cue],
) -> None:
    project = cues[0].project
    composed = project.statuses.get(kind=models.StatusKind.DONE)
    cues[1].status = composed
    cues[1].save()
    song = project.statuses.filter(kind=models.StatusKind.SONG).first()
    cues[2].status = song
    cues[2].save()
    assert tools.compositions_to_mark(cues[:3]) == [cues[0]]
    made = tools.add_versions(cues[:3], tools.VersionOptions())
    assert [v.label for v in made] == ["v2"] * 3
    for cue in cues[:3]:
        cue.refresh_from_db()
        assert _current(cue).spotting_notes == f"Notes for {cue.number}"
    assert cues[0].status == composed
    assert cues[2].status == song
    # One change set: undo takes all three back to v1.
    _undo_last(project)
    for cue in cues[:3]:
        cue.refresh_from_db()
        assert _current(cue).label == "v1"
        assert cue.versions.count() == 1


def test_add_versions_minor_without_notes_or_marking(
    cues: list[models.Cue],
) -> None:
    sub = factories.SubStatusFactory.create(project=cues[0].project)
    _current(cues[0]).sub_status = sub
    _current(cues[0]).save()
    options = tools.VersionOptions(
        minor=True, keep_notes=False, keep_sub_status=False, mark_composed=False
    )
    version = tools.add_versions(cues[:1], options)[0]
    assert version.label == "v1.1"
    assert version.spotting_notes == ""
    assert version.sub_status is None
    cues[0].refresh_from_db()
    assert _status(cues[0]).label == "To Write"


def test_compositions_to_mark_without_a_composed_status() -> None:
    assert tools.compositions_to_mark([]) == []
    project = factories.ProjectFactory.create()
    project.statuses.filter(kind=models.StatusKind.DONE).delete()
    cue = _cue(_reel(project, "1"), "1m01", 0)
    assert tools.compositions_to_mark([cue]) == []
    version = tools.add_versions([cue], tools.VersionOptions())[0]
    assert _status(version.cue).label == "To Write"


def test_add_versions_reopens_approved_cues(cues: list[models.Cue]) -> None:
    project = cues[0].project
    cues[0].status = project.statuses.filter(
        kind=models.StatusKind.APPROVED
    ).first()
    cues[0].save()
    tools.add_versions(
        [cues[0]],
        tools.VersionOptions(
            minor=False,
            keep_notes=True,
            keep_sub_status=True,
            mark_composed=False,
        ),
    )
    cues[0].refresh_from_db()
    assert cues[0].status is not None
    assert cues[0].status.kind == models.StatusKind.DONE


def test_reopening_needs_a_done_status(cues: list[models.Cue]) -> None:
    project = cues[0].project
    approved = project.statuses.filter(kind=models.StatusKind.APPROVED).first()
    cues[0].status = approved
    project.statuses.filter(kind=models.StatusKind.DONE).delete()
    assert authoring.reopened_status(cues[0]) == approved


def test_add_versions_to_a_cue_without_one() -> None:
    project = factories.ProjectFactory.create()
    cue = factories.CueFactory.create(
        project=project, picture=_reel(project, "1")
    )
    version = tools.add_versions([cue], tools.VersionOptions())[0]
    assert version.label == "v1"


def test_split_second_half_and_undo(cues: list[models.Cue]) -> None:
    cue = cues[0]
    cue.seq_start = 10 * SECOND
    cue.save()
    at, number = tools.split_defaults(cue)
    assert at == HOUR + 15 * SECOND
    assert number == "1m01a"
    plan = tools.plan_split(cue, at, number)
    new = tools.split(plan)
    cue.refresh_from_db()
    assert (cue.tc_in, cue.tc_out, cue.seq_start) == (
        HOUR,
        at,
        10 * SECOND,
    )
    assert (new.tc_in, new.tc_out, new.seq_start) == (
        at,
        HOUR + 30 * SECOND,
        None,
    )
    assert new.title == cue.title
    assert new.status == cue.status
    assert _current(new).label == "v1"
    assert cue.versions.count() == 1
    _undo_last(cue.project)
    cue.refresh_from_db()
    assert cue.tc_out == HOUR + 30 * SECOND
    assert not cue.project.cues.filter(number="1m01a").exists()


def test_split_first_half(cues: list[models.Cue]) -> None:
    cue = cues[0]
    cue.seq_start = 10 * SECOND
    cue.save()
    at = HOUR + 10 * SECOND
    plan = tools.plan_split(cue, at, "1m01a", new_takes_second=False)
    assert plan.first == (HOUR, at)
    new = tools.split(plan)
    cue.refresh_from_db()
    assert cue.tc_in == at
    assert cue.seq_start is None
    assert (new.tc_in, new.tc_out, new.seq_start) == (HOUR, at, 10 * SECOND)


def test_split_refusals(cues: list[models.Cue]) -> None:
    cue = cues[0]
    with pytest.raises(tools.ToolError, match="inside the cue"):
        tools.plan_split(cue, _times(cue)[0], "1m01a")
    with pytest.raises(tools.ToolError, match="in use"):
        tools.plan_split(cue, _times(cue)[0] + 1, "1m02")
    cue.tc_out = None
    with pytest.raises(tools.ToolError, match="needs an IN and an OUT"):
        tools.split_defaults(cue)


def test_merge_omits_the_others_and_undo(cues: list[models.Cue]) -> None:
    project = cues[0].project
    tc_in, tc_out, keeper = tools.merge_defaults(cues[1:3])
    assert keeper == cues[1]
    assert (tc_in, tc_out) == (cues[1].tc_in, cues[2].tc_out)
    tools.merge(cues[1:3], keeper, (tc_in, tc_out))
    cues[2].refresh_from_db()
    assert not cues[2].active
    assert _status(cues[2]).kind == models.StatusKind.OMIT
    keeper.refresh_from_db()
    assert keeper.tc_out == tc_out
    _undo_last(project)
    cues[2].refresh_from_db()
    assert cues[2].active


def test_merge_can_delete_the_others(cues: list[models.Cue]) -> None:
    project = cues[0].project
    project.statuses.filter(kind=models.StatusKind.OMIT).delete()
    tools.merge(cues[:3], cues[2], (_times(cues[0])[0], _times(cues[2])[1]))
    assert not project.cues.get(pk=cues[0].pk).active
    tools.merge(
        cues[2:],
        cues[3],
        (_times(cues[2])[0], _times(cues[3])[1]),
        delete_others=True,
    )
    assert not project.cues.filter(pk=cues[2].pk).exists()


def test_merge_refusals(cues: list[models.Cue]) -> None:
    with pytest.raises(tools.ToolError, match="two or more"):
        tools.merge_defaults(cues[:1])
    with pytest.raises(tools.ToolError, match="must be one of"):
        tools.merge(cues[:2], cues[3], (0, 10))
    with pytest.raises(tools.ToolError, match="OUT must be after IN"):
        tools.merge(cues[:2], cues[0], (10, 10))


def test_bump_moves_trailing_numbers() -> None:
    numbers = ["1m01", "1m03", "1m04a", "Main"]
    assert tools.bump(numbers, 1, 1) == ["1m01", "1m04", "1m05a", "Main"]
    assert tools.bump(["1m00", "1m01"], 0, -1) == ["1m00", "1m00"]


def test_overwrite_numbers(cues: list[models.Cue]) -> None:
    cues[1].number = "1m02a"
    project = cues[0].project
    reel_two = _reel(project, "2")
    cues[2].picture = reel_two
    cues[3].tc_in = 2 * HOUR
    numbers = tools.overwrite_numbers(cues, "1m10")
    assert numbers == ["1m10", "1m11a", "1m12", "1m13"]
    numbers = tools.overwrite_numbers(cues, "1m10s", keep_suffix=False)
    assert numbers == ["1m10s", "1m11s", "1m12s", "1m13s"]
    numbers = tools.overwrite_numbers(
        cues, "1m10", reel=tools.ReelPart.CUE_REEL
    )
    assert numbers[2] == "2m12"
    numbers = tools.overwrite_numbers(cues, "1m10", reel=tools.ReelPart.HOUR)
    assert numbers[3] == "2m13"
    cues[0].tc_in = None
    numbers = tools.overwrite_numbers(cues, "1m10", reel=tools.ReelPart.HOUR)
    assert numbers[0] == "1m10"
    with pytest.raises(tools.ToolError, match="no digits"):
        tools.overwrite_numbers(cues, "Main")


def test_hour_numbers_follow_drop_frame_labels() -> None:
    project = factories.ProjectFactory.create(
        frame_rate="29.97", drop_frame=True
    )
    cue = factories.CueFactory.create(
        project=project, tc_in=107_892, tc_out=108_000
    )  # 1:00:00;00
    numbers = tools.overwrite_numbers([cue], "1m01", reel=tools.ReelPart.HOUR)
    assert numbers == ["1m01"]


def test_renumber_swaps_and_undo(cues: list[models.Cue]) -> None:
    project = cues[0].project
    tools.renumber(cues[:2], ["1m02", "1m01"])
    cues[0].refresh_from_db()
    assert cues[0].number == "1m02"
    _undo_last(project)
    for cue in cues[:2]:
        cue.refresh_from_db()
    assert cues[0].number == "1m01"
    tools.renumber(cues[:2], ["1m01", "1m02"])  # nothing to change
    assert models.ChangeSet.objects.filter(project=project).count() == 2
    with pytest.raises(tools.ToolError, match=r"1m03 \(in use\)"):
        tools.renumber(cues[:1], ["1m03"])


def test_shift_by_offset_and_undo(cues: list[models.Cue]) -> None:
    project = cues[0].project
    cues[0].seq_start = 0
    cues[0].save()
    tools.shift(cues[:2], 2 * SECOND)
    cues[0].refresh_from_db()
    assert cues[0].tc_in == HOUR + 2 * SECOND
    assert cues[0].seq_start == 2 * SECOND
    assert _current(cues[0]).tc_in == HOUR  # versions keep theirs
    _undo_last(project)
    cues[0].refresh_from_db()
    assert cues[0].tc_in == HOUR
    tools.shift(cues[:1], 0)
    assert models.ChangeSet.objects.filter(project=project).count() == 2
    tools.check_shift([], -HOUR)


def test_shift_refuses_times_outside_the_range(cues: list[models.Cue]) -> None:
    with pytest.raises(tools.ToolError, match="1m01"):
        tools.shift(cues[:1], -2 * HOUR)


def test_move_to_another_reel(cues: list[models.Cue]) -> None:
    project = cues[0].project
    reel_two = _reel(project, "2")
    tools.move(cues[:2], reel_two)
    cues[0].refresh_from_db()
    assert cues[0].picture == reel_two
    assert cues[0].tc_in == HOUR
    tools.move(cues[:2], reel_two)  # already there
    _undo_last(project)
    cues[0].refresh_from_db()
    assert cues[0].picture == cues[3].picture


def test_copy_current_version(cues: list[models.Cue]) -> None:
    project = cues[0].project
    reel_two = _reel(project, "2")
    theme = models.ThemeOption.objects.create(
        project=project, name="Sea", color="#336699"
    )
    models.VersionTheme.objects.create(version=_current(cues[0]), theme=theme)
    instrument = models.InstrumentOption.objects.create(
        project=project, name="Cello", color="#996633"
    )
    models.CueInstrument.objects.create(cue=cues[0], instrument=instrument)
    assert tools.copy_numbers(cues[:2], reel_two) == ["2m01", "2m02"]
    copies = tools.copy(cues[:2], reel_two)
    first = copies[0]
    assert (first.number, first.picture, first.title) == (
        "2m01",
        reel_two,
        cues[0].title,
    )
    assert _current(first).label == "v1"
    assert _current(first).written_to == reel_two.current_version()
    assert list(_current(first).themes.values_list("theme", flat=True)) == [
        theme.pk
    ]
    assert first.instrumentation.count() == 1
    assert first.versions.count() == 1
    _undo_last(project)
    assert not reel_two.cues.exists()


def test_copy_all_versions(cues: list[models.Cue]) -> None:
    reel_two = _reel(cues[0].project, "2")
    tools.add_versions(cues[:1], tools.VersionOptions())
    copy = tools.copy(cues[:1], reel_two, all_versions=True)[0]
    assert sorted(copy.versions.values_list("label", flat=True)) == [
        "v1",
        "v2",
    ]
    assert _current(copy).label == "v2"


def test_delete_and_undo(cues: list[models.Cue]) -> None:
    project = cues[0].project
    tools.delete(cues[:2])
    assert _numbers(project) == ["1m03", "1m04"]
    assert models.ChangeSet.objects.filter(project=project).latest(
        "pk"
    ).label == ("Delete 2 cues")
    _undo_last(project)
    assert _numbers(project) == ["1m01", "1m02", "1m03", "1m04"]
    tools.delete([project.cues.get(number="1m01")])
    assert models.ChangeSet.objects.filter(project=project).latest(
        "pk"
    ).label == ("Delete cue 1m01")


def test_renumber_lets_cues_keep_numbers_that_dont_fit(
    cues: list[models.Cue],
) -> None:
    models.Cue.objects.filter(pk=cues[0].pk).update(number="zz")
    cues[0].refresh_from_db()
    tools.renumber(cues[:2], ["zz", "1m09"])  # zz keeps its number
    cues[1].refresh_from_db()
    assert cues[1].number == "1m09"
    with pytest.raises(tools.ToolError, match=r"yy \(doesn't fit"):
        tools.renumber(cues[:1], ["yy"])


def test_copy_refuses_numbers_that_dont_fit(cues: list[models.Cue]) -> None:
    project = cues[0].project
    odd = _reel(project, "2")
    _cue(odd, "zz1", 0)  # imported before; the next would be zz2
    with pytest.raises(tools.ToolError, match=r"zz2 \(doesn't fit"):
        tools.copy_numbers(cues[:1], odd)
