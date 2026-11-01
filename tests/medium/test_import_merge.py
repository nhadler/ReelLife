"""The shared NEW/UPDATE/SKIP preview."""

import datetime

import pytest

from cues import models
from cues.importers import base
from cues.importers import merge
from tests import factories

pytestmark = pytest.mark.django_db


def _row(
    label: str, tc_in: int | None, tc_out: int | None, line: int = 0
) -> base.ImportRow:
    number, title = base.split_name(label)
    return base.ImportRow(label, number, title, tc_in, tc_out, line=line)


def _file(*rows: base.ImportRow, **kwargs: object) -> base.ParsedFile:
    return base.ParsedFile("test", list(rows), **kwargs)  # type: ignore[arg-type]


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    """Reel 1 at 1:00:00:00-1:20:00:00, reel 2 at 2:00:00:00-2:20:00:00."""
    project = factories.ProjectFactory.create()
    turnover = factories.TurnoverFactory.create(project=project)
    for n in (1, 2):
        picture = factories.PictureFactory.create(
            project=project, name=str(n), order=n
        )
        factories.PictureVersionFactory.create(
            picture=picture,
            turnover=turnover,
            tc_in=n * 86_400,
            tc_out=n * 86_400 + 28_800,
        )
    return project


def _plan(
    project: models.Project, parsed: base.ParsedFile, **options: object
) -> merge.Preview:
    return merge.plan(project, parsed, merge.Options(**options))  # type: ignore[arg-type]


def test_new_cues_land_in_the_picture_containing_their_in(
    project: models.Project,
) -> None:
    preview = _plan(
        project,
        _file(
            _row("1m01 Main Title", 86_640, 88_572),
            _row("2m01 Diner", 2 * 86_400 + 100, 2 * 86_400 + 900),
            _row("9m01 Nowhere", 9 * 86_400, None),
        ),
    )
    pictures = {p.name: p.pk for p in project.pictures.all()}
    assert [
        (c.action, c.row.number, c.picture_id) for c in preview.changes
    ] == [
        (merge.Action.NEW, "1m01", pictures["1"]),
        (merge.Action.NEW, "2m01", pictures["2"]),
        (merge.Action.NEW, "9m01", None),
    ]
    assert preview.count(merge.Action.NEW) == 3


def test_destination_picture_overrides_placement(
    project: models.Project,
) -> None:
    reel2 = project.pictures.get(name="2")
    preview = _plan(
        project, _file(_row("1m01 x", 86_640, None)), picture_id=reel2.pk
    )
    assert preview.changes[0].picture_id == reel2.pk


def test_existing_cues_are_updated_case_insensitively(
    project: models.Project,
) -> None:
    cue = factories.CueFactory.create(
        project=project,
        picture=project.pictures.get(name="1"),
        number="1M01",
        title="Old",
        tc_in=86_640,
        tc_out=87_000,
    )
    preview = _plan(project, _file(_row("1m01 New", 86_700, 87_000)))
    (change,) = preview.changes
    assert change.action == merge.Action.UPDATE
    assert (change.cue_id, change.old_in, change.old_title) == (
        cue.pk,
        86_640,
        "Old",
    )
    assert change.conform is None


def test_unchanged_cues_are_skipped(project: models.Project) -> None:
    factories.CueFactory.create(
        project=project, number="1m01", tc_in=10, tc_out=20, title="Same"
    )
    preview = _plan(project, _file(_row("1m01 Different", 10, 20)))
    assert preview.changes[0].action == merge.Action.SKIP
    assert preview.changes[0].reason == "unchanged"


def test_missing_timecodes_in_the_file_dont_count_as_changes(
    project: models.Project,
) -> None:
    factories.CueFactory.create(
        project=project, number="1m01", tc_in=10, tc_out=20
    )
    preview = _plan(project, _file(_row("1m01", None, None)))
    assert preview.changes[0].action == merge.Action.SKIP


def test_titles_update_only_when_asked(project: models.Project) -> None:
    factories.CueFactory.create(
        project=project, number="1m01", tc_in=10, tc_out=20, title="Old"
    )
    parsed = _file(_row("1m01 New", 10, 20))
    assert _plan(project, parsed).changes[0].action == merge.Action.SKIP
    updated = _plan(project, parsed, update_titles=True).changes[0]
    assert updated.action == merge.Action.UPDATE


def test_seq_start_change_is_an_update(project: models.Project) -> None:
    factories.CueFactory.create(
        project=project, number="1m01", tc_in=10, tc_out=20
    )
    row = base.ImportRow("1m01", "1m01", "", 10, 20, seq_start=5)
    assert _plan(project, _file(row)).changes[0].action == merge.Action.UPDATE


def test_duplicate_numbers_merge_to_earliest_in_latest_out(
    project: models.Project,
) -> None:
    preview = _plan(
        project,
        _file(
            _row("1m03 Aftermath", 95_079, 97_200, line=3),
            _row("1m03 Aftermath", 97_440, 98_400, line=4),
            _row("1M03", 95_100, None, line=5),
        ),
    )
    (change,) = preview.changes
    assert (change.row.tc_in, change.row.tc_out) == (95_079, 98_400)
    assert change.row.title == "Aftermath"
    assert change.row.line == 3
    # Ten seconds of silence between the clips (at 24 fps) is worth a look.
    assert preview.warnings == [
        "1m03: 3 clips merged into one cue with 0:10 of silence between "
        "them. If they're separate uses, give them different numbers."
    ]


def test_clips_that_touch_merge_without_a_warning(
    project: models.Project,
) -> None:
    second = project.rate.nominal
    preview = _plan(
        project,
        _file(
            _row("1m04 Drive", 100_000, 100_480),
            _row("1m04 Drive", 100_480 + second - 1, 101_000),  # < 1 s
            _row("1m04 Drive", 100_200, 100_300),  # inside the first
            _row("1m05 Alone", 102_000, 102_500),
        ),
    )
    assert not preview.warnings


def test_invalid_numbers_are_excluded_and_listed(
    project: models.Project,
) -> None:
    bad = _row("DX_01 Dialogue", 1, 2)
    preview = _plan(project, _file(_row("1m01 x", 1, 2), bad))
    assert [c.row.number for c in preview.changes] == ["1m01"]
    assert preview.excluded == [bad]
    # A cue the project has is updated whatever its number.
    factories.CueFactory.create(project=project, number="DX_01")
    update = _plan(project, _file(bad))
    assert update.changes[0].row.number == "DX_01"
    assert not update.excluded


def test_number_only_mapping_leaves_titles(project: models.Project) -> None:
    preview = _plan(
        project,
        _file(_row("1m01 Main Title", 1, 2)),
        mapping=merge.Mapping.NUMBER_ONLY,
    )
    assert preview.changes[0].row.title == ""


def test_title_only_mapping_numbers_in_timeline_order(
    project: models.Project,
) -> None:
    preview = _plan(
        project,
        _file(
            _row("Second cue", 200, 300, line=1),
            _row("First cue", 100, 150, line=2),
            _row("No time", None, None, line=3),
        ),
        mapping=merge.Mapping.TITLE_ONLY,
        start_number="3m08",
    )
    assert [(c.row.number, c.row.title) for c in preview.changes] == [
        ("3m08", "First cue"),
        ("3m09", "Second cue"),
        ("3m10", "No time"),
    ]


def test_missing_cues_are_listed_not_deleted(project: models.Project) -> None:
    kept = factories.CueFactory.create(project=project, number="1m01")
    gone = factories.CueFactory.create(
        project=project, number="1m02", title="Gone"
    )
    factories.CueFactory.create(project=project, number="1m03", active=False)
    preview = _plan(project, _file(_row("1m01", kept.tc_in, kept.tc_out)))
    assert preview.missing == [merge.MissingCue(gone.pk, "1m02", "Gone")]


def test_frame_rate_mismatch_is_refused(project: models.Project) -> None:
    parsed = _file(_row("1m01", 1, 2), frame_rate="29.97", drop_frame=True)
    with pytest.raises(merge.RateMismatchError, match="29.97 DF.*23.976"):
        _plan(project, parsed)


def test_matching_frame_rate_is_accepted(project: models.Project) -> None:
    parsed = _file(_row("1m01", 1, 2), frame_rate="23.976", drop_frame=False)
    assert _plan(project, parsed).count(merge.Action.NEW) == 1


def test_updates_to_cues_on_older_picture_show_the_conform(
    project: models.Project,
) -> None:
    reel1 = project.pictures.get(name="1")
    old_cut = reel1.current_version()
    assert old_cut is not None
    cue = factories.CueFactory.create(
        project=project,
        picture=reel1,
        number="1m01",
        tc_in=86_640,
        tc_out=88_000,
    )
    cue.current_version = factories.CueVersionFactory.create(
        cue=cue, written_to=old_cut, tc_in=86_640, tc_out=88_000
    )
    cue.save()
    new_turnover = factories.TurnoverFactory.create(
        project=project, received_date=datetime.date(2027, 1, 1)
    )
    factories.PictureVersionFactory.create(
        picture=reel1, turnover=new_turnover, tc_in=86_460, tc_out=115_260
    )
    preview = _plan(project, _file(_row("1m01", 86_700, 88_036)))
    conform = preview.changes[0].conform
    assert conform is not None
    assert (conform.start_offset, conform.duration_difference) == (60, -24)


def test_warnings_pass_through(project: models.Project) -> None:
    parsed = _file(_row("1m01", None, None), warnings=["line 3: bad IN"])
    assert _plan(project, parsed).warnings == ["line 3: bad IN"]


def test_rows_without_a_number_are_dropped(project: models.Project) -> None:
    preview = _plan(project, _file(_row("", 1, 2)))
    assert not preview.changes
