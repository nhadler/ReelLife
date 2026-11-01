"""Progress and conform helpers that read the database."""

import datetime

import pytest

from cues import models
from cues.services import demo
from cues.services import progress
from tests import factories

pytestmark = pytest.mark.django_db


def test_for_project_counts_statuses_and_settings() -> None:
    project = factories.ProjectFactory.create()
    done = project.statuses.get(label="Composed")
    song = project.statuses.get(label="Song")
    factories.CueFactory.create(
        project=project, status=done, tc_in=0, tc_out=2_040
    )
    factories.CueFactory.create(project=project, tc_in=0, tc_out=3_176)
    factories.CueFactory.create(project=project, tc_in=None, tc_out=None)
    factories.CueFactory.create(project=project, status=song)
    result = progress.for_project(project)
    assert (result.composed.cues, result.total.cues) == (1, 3)
    assert result.percent == 39

    settings = models.AppSettings.load()
    settings.music_totals = models.AppSettings.MusicTotals.ALL
    settings.save()
    assert progress.for_project(project).total.cues == 4


def test_current_picture_versions_and_runtime() -> None:
    project = demo.build_demo().project
    current = progress.current_picture_versions(project)
    reels = {p.name: p for p in project.pictures.all()}
    assert set(current) == {reel.pk for reel in reels.values()}
    for reel in reels.values():
        version = reel.current_version()
        assert version is not None
        assert current[reel.pk] == version.pk
    # Reel 1 from c0917, reels 2 and 3 from c1030 (23.976 frames).
    expected = sum(
        (v.tc_out or 0) - (v.tc_in or 0)
        for v in models.PictureVersion.objects.filter(id__in=current.values())
    )
    assert progress.runtime(project) == expected > 0


def test_sequence_numbers_follow_picture_then_in() -> None:
    project = factories.ProjectFactory.create()
    reel2 = factories.PictureFactory.create(project=project, name="2", order=2)
    reel1 = factories.PictureFactory.create(project=project, name="1", order=1)
    late = factories.CueFactory.create(
        project=project, picture=reel1, number="1m02", tc_in=500
    )
    early = factories.CueFactory.create(
        project=project, picture=reel1, number="1m01", tc_in=100
    )
    other = factories.CueFactory.create(
        project=project, picture=reel2, number="2m01", tc_in=0
    )
    factories.CueFactory.create(
        project=project, picture=reel1, number="1m00", active=False
    )
    assert progress.sequence_numbers(project) == {
        early.pk: 1,
        late.pk: 2,
        other.pk: 3,
    }


def test_current_picture_version_ties_break_by_turnover_id() -> None:
    picture = factories.PictureFactory.create()
    same_day = datetime.date(2026, 3, 1)
    first = factories.TurnoverFactory.create(
        project=picture.project, received_date=same_day
    )
    second = factories.TurnoverFactory.create(
        project=picture.project, received_date=same_day
    )
    factories.PictureVersionFactory.create(picture=picture, turnover=first)
    newest = factories.PictureVersionFactory.create(
        picture=picture, turnover=second
    )
    assert progress.current_picture_versions(picture.project) == {
        picture.pk: newest.pk
    }
    assert picture.current_version() == newest
