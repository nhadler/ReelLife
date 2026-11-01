"""Model rules and constraints (12.1 "Model / service")."""

import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction
from django.db.models import ProtectedError
import pytest

from cues import models
from cues import timecode
from cues.services import projects
from tests import factories

pytestmark = pytest.mark.django_db


def _integrity_error(make: object) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make()  # type: ignore[operator]


# Projects


def test_create_project_seeds_statuses_and_sub_statuses() -> None:
    project = projects.create_project(title="The Big Heist")
    labels = list(project.statuses.values_list("label", "kind"))
    assert len(labels) == 17
    assert labels[0] == ("To Write", "to_do")
    assert ("Composed", "done") in labels
    assert ("Stems", "approved") in labels
    assert ("Omit", "omit") in labels
    assert list(project.sub_statuses.values_list("label", flat=True)) == [
        "Awaiting Notes",
        "Conform",
        "Delivered",
        "Respot",
        "Reuse",
        "To Be Presented",
        "Tracked",
    ]
    status = projects.default_status(project)
    assert status is not None
    assert status.label == "To Write"


def test_seeding_twice_adds_nothing() -> None:
    project = projects.create_project(title="Film")
    projects.seed_project(project)
    assert project.statuses.count() == 17
    assert project.sub_statuses.count() == 7


def test_default_status_falls_back_to_first() -> None:
    project = factories.ProjectFactory.create()
    project.statuses.filter(label="To Write").delete()
    status = projects.default_status(project)
    assert status is not None
    assert status.label == "In Progress"


def test_create_project_validates() -> None:
    with pytest.raises(ValidationError) as error:
        projects.create_project(title="x", frame_rate="24", drop_frame=True)
    assert "drop_frame" in error.value.message_dict
    assert not models.Project.objects.exists()


def test_cue_number_pattern_must_compile() -> None:
    project = models.Project(title="x", cue_number_pattern="([")
    with pytest.raises(ValidationError) as error:
        project.full_clean()
    assert "cue_number_pattern" in error.value.message_dict


def test_drop_frame_needs_a_drop_frame_rate_in_the_database() -> None:
    _integrity_error(
        lambda: models.Project.objects.create(
            title="x", frame_rate="25", drop_frame=True
        )
    )


def test_lanes_capped_at_8() -> None:
    _integrity_error(lambda: models.Project.objects.create(title="x", lanes=9))


def test_project_rate() -> None:
    project = models.Project(frame_rate="29.97", drop_frame=True)
    assert project.rate == timecode.RATE_29_97_DF


def test_str_methods() -> None:
    project = factories.ProjectFactory.create(
        title="Heist", production_number="BH1"
    )
    cue = factories.CueFactory.create(
        project=project, number="1m01", title="Main"
    )
    version = factories.CueVersionFactory.create(cue=cue, label="v1")
    pv = factories.PictureVersionFactory.create(picture=cue.picture)
    assert str(project) == "BH1 Heist"
    assert str(cue) == "1m01 Main"
    assert str(version) == "1m01 v1"
    assert str(pv) == f"{cue.picture} · {pv.turnover}"
    assert str(models.Series(title="S")) == "S"
    assert str(models.Deadline(kind="mix", end_date=datetime.date.today())) == (
        "Mix"
    )
    assert str(models.DayOff(date=datetime.date(2026, 12, 25))) == "2026-12-25"
    assert str(project.statuses.first()) == "To Write"
    assert str(project.sub_statuses.first()) == "Awaiting Notes"
    assert str(models.View(name="Overview")) == "Overview"
    assert str(models.AppSettings.load()) == "Settings"


# Statuses


@pytest.mark.parametrize(
    ("kind", "category", "composed", "counted"),
    [
        ("to_do", "unapproved", False, True),
        ("changes", "unapproved", True, True),
        ("done", "unapproved", True, True),
        ("completed", "approved", True, True),
        ("omit", "inactive", False, False),
        ("song", "song", False, True),
    ],
)
def test_status_kind_semantics(
    kind: str, category: str, composed: bool, counted: bool
) -> None:
    status = models.StatusOption(kind=kind)
    assert status.category == category
    assert status.counts_as_composed is composed
    assert status.in_totals is counted


def test_status_in_use_cannot_be_deleted() -> None:
    cue = factories.CueFactory.create()
    status = cue.project.statuses.get(label="To Write")
    cue.status = status
    cue.save()
    with pytest.raises(ProtectedError):
        status.delete()


def test_status_colour_must_be_hex() -> None:
    status = factories.StatusOptionFactory.build(color="red")
    with pytest.raises(ValidationError):
        status.full_clean(exclude=["project"])


# Cues


def test_cue_numbers_are_unique_among_active_cues() -> None:
    cue = factories.CueFactory.create(number="1m01")
    _integrity_error(
        lambda: factories.CueFactory.create(
            project=cue.project, picture=cue.picture, number="1M01"
        )
    )


def test_inactive_cues_may_share_a_number() -> None:
    cue = factories.CueFactory.create(number="1m01", active=False)
    factories.CueFactory.create(
        project=cue.project, picture=cue.picture, number="1m01"
    )
    factories.CueFactory.create(
        project=cue.project, picture=cue.picture, number="1m01", active=False
    )
    assert cue.project.cues.filter(number="1m01").count() == 3


def test_numbers_may_repeat_across_projects() -> None:
    factories.CueFactory.create(number="1m01")
    factories.CueFactory.create(number="1m01")


@pytest.mark.parametrize(
    "factory",
    [
        factories.CueFactory,
        factories.CueVersionFactory,
        factories.PictureVersionFactory,
    ],
)
def test_out_cannot_precede_in(factory: object) -> None:
    _integrity_error(lambda: factory.create(tc_in=100, tc_out=99))  # type: ignore[attr-defined]


@pytest.mark.parametrize("field", ["tc_in", "seq_start"])
def test_cue_frames_are_non_negative(field: str) -> None:
    _integrity_error(lambda: factories.CueFactory.create(**{field: -1}))


def test_duration() -> None:
    cue = models.Cue(tc_in=90_720, tc_out=93_896)
    assert cue.duration == 3_176
    assert models.Cue(tc_in=1).duration is None
    assert models.CueVersion(tc_in=1, tc_out=11).duration == 10
    assert models.CueVersion(tc_out=1).duration is None


def test_cue_clean_rejects_other_projects_rows() -> None:
    cue = factories.CueFactory.create()
    other = factories.ProjectFactory.create()
    cue.picture = factories.PictureFactory.create(project=other)
    cue.status = other.statuses.first()
    cue.current_version = factories.CueVersionFactory.create()
    with pytest.raises(ValidationError) as error:
        cue.full_clean()
    assert set(error.value.message_dict) == {
        "picture",
        "status",
        "current_version",
    }


def test_cue_clean_accepts_own_rows() -> None:
    cue = factories.CueFactory.create()
    cue.status = cue.project.statuses.first()
    cue.current_version = factories.CueVersionFactory.create(cue=cue)
    cue.full_clean()


def test_cue_version_written_to_must_be_same_project() -> None:
    version = factories.CueVersionFactory.create()
    version.written_to = factories.PictureVersionFactory.create()
    with pytest.raises(ValidationError) as error:
        version.full_clean()
    assert "written_to" in error.value.message_dict
    version.written_to = factories.PictureVersionFactory.create(
        picture=version.cue.picture
    )
    version.full_clean()


def test_version_labels_unique_per_cue() -> None:
    version = factories.CueVersionFactory.create(label="v1")
    _integrity_error(
        lambda: factories.CueVersionFactory.create(cue=version.cue, label="v1")
    )


def test_deleting_current_version_clears_it() -> None:
    cue = factories.CueFactory.create()
    cue.current_version = factories.CueVersionFactory.create(cue=cue)
    cue.save()
    cue.current_version.delete()
    cue.refresh_from_db()
    assert cue.current_version is None


# Pictures and turnovers


def test_picture_version_must_match_turnover_project() -> None:
    picture = factories.PictureFactory.create()
    version = models.PictureVersion(
        picture=picture, turnover=factories.TurnoverFactory.create()
    )
    with pytest.raises(ValidationError) as error:
        version.full_clean()
    assert "turnover" in error.value.message_dict


def test_one_picture_version_per_turnover() -> None:
    pv = factories.PictureVersionFactory.create()
    _integrity_error(
        lambda: factories.PictureVersionFactory.create(
            picture=pv.picture, turnover=pv.turnover
        )
    )


def test_current_picture_version_is_from_latest_turnover() -> None:
    reel1 = factories.PictureFactory.create(name="1")
    project = reel1.project
    reel2 = factories.PictureFactory.create(project=project, name="2")
    t1 = factories.TurnoverFactory.create(
        project=project, received_date=datetime.date(2026, 1, 1)
    )
    t2 = factories.TurnoverFactory.create(
        project=project, received_date=datetime.date(2026, 2, 1)
    )
    old1 = factories.PictureVersionFactory.create(picture=reel1, turnover=t1)
    new1 = factories.PictureVersionFactory.create(picture=reel1, turnover=t2)
    only2 = factories.PictureVersionFactory.create(picture=reel2, turnover=t1)
    assert reel1.current_version() == new1
    assert reel2.current_version() == only2  # t2 skipped reel 2
    assert old1 != new1
    assert factories.PictureFactory.create().current_version() is None


def test_picture_names_unique_per_project() -> None:
    picture = factories.PictureFactory.create(name="1")
    _integrity_error(
        lambda: factories.PictureFactory.create(
            project=picture.project, name="1"
        )
    )


def test_deadline_cannot_end_before_it_starts() -> None:
    _integrity_error(
        lambda: factories.DeadlineFactory.create(
            start_date=datetime.date(2026, 5, 2),
            end_date=datetime.date(2026, 5, 1),
        )
    )


# Views and settings


def test_global_views_are_seeded() -> None:
    names = list(
        models.View.objects.filter(project=None).values_list("name", flat=True)
    )
    # The eight standard views, in order.
    assert names == [
        "Standard",
        "Spotting",
        "Conform",
        "Orchestration",
        "Recording",
        "Mixing",
        "Viewer",
        "Cue Sheet",
    ]
    for view in models.View.objects.filter(project=None):
        view.full_clean()


def test_global_view_names_are_unique() -> None:
    _integrity_error(lambda: models.View.objects.create(name="Standard"))


def test_project_views_may_reuse_global_names() -> None:
    factories.ViewFactory.create(name="Standard")


@pytest.mark.parametrize(
    "columns", [[{"id": "nope"}], ["number"], {"id": "number"}]
)
def test_view_columns_must_be_known(columns: object) -> None:
    view = factories.ViewFactory.build(columns=columns, project=None)
    with pytest.raises(ValidationError) as error:
        view.full_clean()
    assert "columns" in error.value.message_dict


def test_settings_is_a_single_row() -> None:
    first = models.AppSettings.load()
    assert models.AppSettings.load().pk == first.pk == 1
    assert first.version_increment == "major"
    _integrity_error(lambda: models.AppSettings.objects.create(pk=2))


# History


def test_cue_changes_are_recorded() -> None:
    cue = factories.CueFactory.create(title="Old")
    cue.title = "New"
    cue.save()
    titles = [h.title for h in cue.history.order_by("history_id")]
    assert titles == ["Old", "New"]


def test_sub_status_labels_are_unique_in_a_project() -> None:
    option = factories.SubStatusFactory.create(label="Respot 2")
    _integrity_error(
        lambda: factories.SubStatusFactory.create(
            project=option.project, label="RESPOT 2"
        )
    )


@pytest.mark.parametrize(
    "factory",
    [
        factories.ProjectFactory,
        factories.TurnoverFactory,
        factories.PictureFactory,
        factories.PictureVersionFactory,
        factories.CueVersionFactory,
    ],
)
def test_history_is_kept_for(factory: object) -> None:
    obj = factory.create()  # type: ignore[attr-defined]
    assert obj.history.count() == 1
