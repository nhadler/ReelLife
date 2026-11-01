"""Importing end to end: parse, preview, apply, undo."""

import dataclasses
import datetime

from django.core.exceptions import ValidationError
import pytest

from accounts.models import AuditEvent
from cues import models
from cues import timecode
from cues.importers import apply
from cues.importers import base
from cues.importers import merge
from cues.importers import protools
from cues.importers import tabular
from cues.services import history
from cues.services import imports
from cues.services import projects
from tests import factories
from tests import golden

pytestmark = pytest.mark.django_db
CLIPS = golden.FIXTURES / "protools_clips_23976.txt"
CSV = golden.FIXTURES / "cue_list.csv"


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    """A 23.976 film with reel 1 (1:00:00:00-1:20:00:00) and reel 2."""
    project = factories.ProjectFactory.create(frame_rate="23.976")
    turnover = factories.TurnoverFactory.create(
        project=project, received_date=datetime.date(2026, 9, 17)
    )
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


def _clips() -> base.ParsedFile:
    return protools.parse(
        protools.decode(CLIPS.read_bytes()), method=protools.Method.CLIPS
    )


def _csv() -> base.ParsedFile:
    return tabular.parse(CSV.read_bytes(), rate=timecode.RATE_23_976)


def _numbers(
    project: models.Project,
) -> list[tuple[str, str, int | None, int | None]]:
    return list(
        project.cues.order_by("number").values_list(
            "number", "title", "tc_in", "tc_out"
        )
    )


def test_pro_tools_import_creates_merged_cues(project: models.Project) -> None:
    preview = merge.plan(project, _clips(), merge.Options())
    result = apply.apply(project, preview, filename="reel1.txt")
    assert (result.created, result.updated) == (3, 0)
    assert _numbers(project) == [
        ("1m01", "Main Title", 86_640, 88_572),
        ("1m02", "The Chase", 90_720, 93_896),
        ("1m03", "Aftermath", 95_079, 98_400),
    ]
    reel1 = project.pictures.get(name="1")
    assert set(project.cues.values_list("picture", flat=True)) == {reel1.pk}
    assert all(
        c.status and c.status.label == "To Write" for c in project.cues.all()
    )
    assert result.change_set.label == "Import reel1.txt"
    event = AuditEvent.objects.get(action="import")
    assert event.detail["created"] == 3


def test_reimport_updates_moved_cues_and_undo_reverts(
    project: models.Project,
) -> None:
    apply.apply(project, merge.plan(project, _clips(), merge.Options()))
    moved = _clips()
    shifted = [
        base.ImportRow(
            r.label,
            r.number,
            r.title,
            (r.tc_in or 0) + 60,
            (r.tc_out or 0) + 60,
            line=r.line,
        )
        for r in moved.rows
    ]
    preview = merge.plan(
        project, base.ParsedFile(moved.source, shifted), merge.Options()
    )
    assert preview.count(merge.Action.UPDATE) == 3
    result = apply.apply(project, preview, filename="reel1 v2.txt")
    assert result.updated == 3
    assert project.cues.get(number="1m01").tc_in == 86_700

    history.undo(result.change_set)
    assert project.cues.get(number="1m01").tc_in == 86_640


def test_undo_removes_an_import_entirely(project: models.Project) -> None:
    result = apply.apply(project, merge.plan(project, _csv(), merge.Options()))
    assert project.cues.exists()
    history.undo(result.change_set)
    assert not project.cues.exists()
    assert not models.CueVersion.objects.filter(cue__project=project).exists()


def test_spreadsheet_fields_fill_new_cues(project: models.Project) -> None:
    preview = merge.plan(project, _csv(), merge.Options())
    assert [r.number for r in preview.excluded] == ["bad row"]
    result = apply.apply(project, preview)
    assert result.created == 4
    main = project.cues.get(number="1m01")
    assert main.status is not None and main.status.label == "Composed"
    assert main.current_version is not None
    assert main.current_version.label == "v2"
    assert main.current_version.spotting_notes == "Big opening"
    reel1 = project.pictures.get(name="1").current_version()
    assert main.current_version.written_to == reel1
    plain = project.cues.get(number="1m03").current_version
    assert plain is not None and plain.label == "v1"  # every cue has one
    # 1:20:05:10 falls between reel 1's OUT and reel 2's IN.
    diner = project.cues.get(number="2m04")
    assert diner.picture.name == projects.PLACEHOLDER_PICTURE


def test_unknown_values_fall_back_with_warnings(
    project: models.Project,
) -> None:
    row = base.ImportRow(
        "1m01",
        "1m01",
        "",
        86_500,
        86_600,
        fields={
            "status": "Mystery",
            "cue_type": "jingle",
            "usage": "Visual Vocal",
        },
    )
    preview = merge.plan(
        project, base.ParsedFile("test", [row]), merge.Options()
    )
    result = apply.apply(project, preview)
    cue = project.cues.get()
    assert cue.status is not None and cue.status.label == "To Write"
    assert (cue.cue_type, cue.usage) == ("SCORE", "VV")
    assert result.warnings == [
        "1m01: unknown status 'Mystery'; used To Write.",
        "1m01: unknown cue type 'jingle'; used SCORE.",
    ]
    # The preview shows them before anything is applied.
    shown = imports.plan(
        project,
        base.ParsedFile("test", [dataclasses.replace(row, number="1m02")]),
        merge.Options(),
    )
    assert shown.warnings == [
        "1m02: unknown status 'Mystery'; used To Write.",
        "1m02: unknown cue type 'jingle'; used SCORE.",
    ]


def test_a_registered_title_column_fills_the_cue_sheet_title(
    project: models.Project,
) -> None:
    sheet = (
        b"Cue,Title,Start,End,Registered Title\n"
        b"1m01,Open,1:00:01:00,1:00:09:00,The Crossing\n"
    )
    parsed = tabular.parse(sheet, rate=timecode.RATE_23_976)
    apply.apply(project, merge.plan(project, parsed, merge.Options()))
    assert project.cues.get().cue_sheet_title == "The Crossing"


def test_a_reimport_in_another_reel_moves_the_cue(
    project: models.Project,
) -> None:
    parsed = base.ParsedFile(
        "test", [base.ImportRow("1m01", "1m01", "", 86_500, 86_600)]
    )
    apply.apply(project, merge.plan(project, parsed, merge.Options()))
    cue = project.cues.get()
    reel_2 = project.pictures.get(name="2")
    later = base.ParsedFile(
        "test",
        [base.ImportRow("1m01", "1m01", "", 2 * 86_400 + 10, 2 * 86_400 + 90)],
    )
    preview = merge.plan(project, later, merge.Options())
    assert preview.changes[0].picture_id == reel_2.pk
    apply.apply(project, preview)
    cue.refresh_from_db()
    assert cue.picture == reel_2


def test_a_stale_preview_no_longer_confirms(project: models.Project) -> None:
    parsed = base.ParsedFile(
        "test", [base.ImportRow("1m01", "1m01", "", 86_500, 86_600)]
    )
    apply.apply(project, merge.plan(project, parsed, merge.Options()))
    later = base.ParsedFile(
        "test", [base.ImportRow("1m01", "1m01", "", 86_700, 86_800)]
    )
    code = imports.fingerprint(merge.plan(project, later, merge.Options()))
    cue = project.cues.get()
    cue.tc_in = 86_550  # someone fixes the IN after the preview was made
    cue.save()
    assert (
        imports.fingerprint(merge.plan(project, later, merge.Options())) != code
    )


def test_cues_without_a_picture_go_to_the_placeholder(
    project: models.Project,
) -> None:
    rows = [
        base.ImportRow("9m01", "9m01", "Far", 9 * 86_400, None),
        base.ImportRow("9m02", "9m02", "Further", 9 * 86_400 + 1, None),
    ]
    preview = merge.plan(
        project, base.ParsedFile("test", rows), merge.Options()
    )
    apply.apply(project, preview)
    pictures = set(project.cues.values_list("picture__name", flat=True))
    assert pictures == {projects.PLACEHOLDER_PICTURE}
    placeholder = projects.placeholder_picture(project)
    assert project.pictures.filter(pk=placeholder.pk).count() == 1


def test_destination_picture_is_used(project: models.Project) -> None:
    reel2 = project.pictures.get(name="2")
    preview = merge.plan(project, _clips(), merge.Options(picture_id=reel2.pk))
    apply.apply(project, preview)
    assert set(project.cues.values_list("picture", flat=True)) == {reel2.pk}


def test_update_without_a_cue_is_rejected(project: models.Project) -> None:
    row = base.ImportRow("1m01", "1m01", "", 1, 2)
    broken = merge.Preview(
        "test", [merge.Change(merge.Action.UPDATE, row=row)], [], [], []
    )
    with pytest.raises(ValueError, match="existing cue"):
        apply.apply(project, broken)
    assert not models.ChangeSet.objects.exists()


def test_every_new_cue_gets_v1(project: models.Project) -> None:
    apply.apply(project, merge.plan(project, _clips(), merge.Options()))
    labels = set(
        models.CueVersion.objects.filter(cue__project=project).values_list(
            "label", flat=True
        )
    )
    assert labels == {"v1"}


def test_only_included_changes_are_applied(project: models.Project) -> None:
    preview = merge.plan(project, _clips(), merge.Options())
    result = apply.apply(project, preview, include={1})
    assert result.created == 1
    assert [n for n, *_ in _numbers(project)] == ["1m02"]


def test_titles_update_when_asked(project: models.Project) -> None:
    factories.CueFactory.create(
        project=project, number="1m01", title="Old", tc_in=86_640, tc_out=88_572
    )
    preview = merge.plan(project, _clips(), merge.Options(update_titles=True))
    apply.apply(project, preview, include={0})
    assert project.cues.get(number="1m01").title == "Main Title"


@pytest.mark.parametrize(
    ("action", "field", "expected"),
    [
        (apply.MissingAction.LEAVE, "active", True),
        (apply.MissingAction.INACTIVE, "active", False),
        (apply.MissingAction.OMIT, "status__kind", "omit"),
    ],
)
def test_missing_cue_actions(
    project: models.Project,
    action: apply.MissingAction,
    field: str,
    expected: object,
) -> None:
    gone = factories.CueFactory.create(project=project, number="1m09")
    preview = merge.plan(project, _clips(), merge.Options())
    result = apply.apply(project, preview, missing_action=action)
    value = project.cues.filter(pk=gone.pk).values_list(field, flat=True).get()
    assert value == expected
    assert result.missing_changed == (
        0 if action == apply.MissingAction.LEAVE else 1
    )


def test_missing_actions_can_be_limited(project: models.Project) -> None:
    first = factories.CueFactory.create(project=project, number="1m08")
    second = factories.CueFactory.create(project=project, number="1m09")
    preview = merge.plan(project, _clips(), merge.Options())
    apply.apply(
        project,
        preview,
        missing_action=apply.MissingAction.INACTIVE,
        missing_ids={second.pk},
    )
    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.active, second.active) == (True, False)


def test_omit_without_an_omit_status_changes_nothing(
    project: models.Project,
) -> None:
    project.statuses.filter(kind="omit").delete()
    factories.CueFactory.create(project=project, number="1m09")
    preview = merge.plan(project, _clips(), merge.Options())
    result = apply.apply(
        project, preview, missing_action=apply.MissingAction.OMIT
    )
    assert result.missing_changed == 0


def test_skipped_rows_are_never_applied(project: models.Project) -> None:
    apply.apply(project, merge.plan(project, _clips(), merge.Options()))
    again = merge.plan(project, _clips(), merge.Options())
    assert again.count(merge.Action.SKIP) == 3
    result = apply.apply(project, again)
    assert (result.created, result.updated) == (0, 0)


def _sheet(text: str) -> base.ParsedFile:
    return tabular.parse(text.encode(), rate=timecode.RATE_23_976)


def test_cue_log_columns_fill_new_cues(project: models.Project) -> None:
    parsed = _sheet(
        "Cue #,Title,IN,OUT,Temp,Key,BPM,Meter,IN Notes,OUT Notes,Scene,"
        "Scene Description,Priority,Difficulty,Artist\n"
        "1m01,Main Title,01:00:10:00,01:01:30:12,Zimmer,D minor,92,4/4,"
        "On the cut,Hard out,12,Storm hits,High,Hard,Nina\n"
    )
    result = apply.apply(project, merge.plan(project, parsed, merge.Options()))
    assert not result.warnings
    cue = project.cues.get(number="1m01")
    assert (cue.scene, cue.priority, cue.difficulty, cue.artist) == (
        "12",
        "High",
        "Hard",
        "Nina",
    )
    version = cue.current_version
    assert version is not None
    assert (version.temp, version.key, str(version.bpm), version.meter) == (
        "Zimmer",
        "D minor",
        "92.00",
        "4/4",
    )
    assert (version.in_note, version.out_note) == ("On the cut", "Hard out")
    assert version.scene_description == "Storm hits"


@pytest.mark.parametrize("bpm", ["fast", "0", "1e9", "NaN"])
def test_bad_bpm_is_left_out_with_a_warning(
    project: models.Project, bpm: str
) -> None:
    parsed = _sheet(f"Cue #,IN,BPM,Priority\n1m01,01:00:10:00,{bpm},Urgent\n")
    result = apply.apply(project, merge.plan(project, parsed, merge.Options()))
    assert f"1m01: BPM {bpm!r} isn't a number; left out." in result.warnings
    assert any("unknown priority" in w for w in result.warnings)
    cue = project.cues.get(number="1m01")
    assert cue.current_version is not None
    assert cue.current_version.bpm is None
    assert cue.priority == ""


def test_values_too_long_for_a_version_stop_the_import(
    project: models.Project,
) -> None:
    parsed = _sheet(f"Cue #,IN,Key\n1m01,01:00:10:00,{'x' * 40}\n")
    with pytest.raises(ValidationError):
        apply.apply(project, merge.plan(project, parsed, merge.Options()))
    assert not project.cues.exists()
