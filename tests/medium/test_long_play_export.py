"""Long Play Time on the cue log: reels on one timeline."""

from collections.abc import Callable
import csv
import datetime
import io
from typing import Any

from django.test import Client
from django.urls import reverse
import openpyxl
import pytest

from accounts.models import Role
from cues import models
from cues.exporters import cue_log
from cues.exporters import pdf
from cues.services import long_play
from cues.services import projects as project_service
from tests import factories

pytestmark = pytest.mark.django_db

HOUR = 86_400  # frames at 24 fps
MINUTE = 60 * 24
TODAY = datetime.date(2026, 11, 1)
POSITIONS = ["number", "tc_in", "tc_out", "duration", "version_in"]


@pytest.fixture(name="viewer")
def fixture_viewer(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.VIEWER)


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    """Reel 1 (20 min) and 2 (18 min) on a turnover; reel 3 and N/A not.

    Cues: 1m01 10 s into reel 1, 2m01 5 s into reel 2, 3m01 on reel 3
    and 0m01 on the placeholder.
    """
    project = factories.ProjectFactory.create(
        frame_rate="24", production_number="LP1", title="Joined"
    )
    turnover = factories.TurnoverFactory.create(
        project=project, received_date=datetime.date(2026, 10, 1)
    )
    project_service.placeholder_picture(project)
    for n, minutes in ((1, 20), (2, 18), (3, 0)):
        picture = factories.PictureFactory.create(
            project=project, name=str(n), order=n
        )
        if minutes:
            factories.PictureVersionFactory.create(
                picture=picture,
                turnover=turnover,
                tc_in=n * HOUR,
                tc_out=n * HOUR + minutes * MINUTE,
            )
    for number, name, tc_in in (
        ("0m01", models.PLACEHOLDER_PICTURE, HOUR),
        ("1m01", "1", HOUR + 240),
        ("2m01", "2", 2 * HOUR + 120),
        ("3m01", "3", 3 * HOUR),
    ):
        cue = factories.CueFactory.create(
            project=project,
            picture=project.pictures.get(name=name),
            number=number,
            tc_in=tc_in,
            tc_out=tc_in + MINUTE,
        )
        cue.current_version = factories.CueVersionFactory.create(cue=cue)
        cue.save()
    return project


def _lines(log: cue_log.CueLog) -> dict[str, list[str]]:
    return {line[0]: line[1:] for reel in log.reels for line in reel.rows}


def test_positions_join_the_reels_after_the_leader(
    project: models.Project,
) -> None:
    log = cue_log.build(
        project,
        POSITIONS,
        long_play_time=long_play.Options(start_hour=1, leader=192),
    )
    lines = _lines(log)
    # 1:00:00:00 + 0:00:08:00 leader + 10 s into reel 1.
    assert lines["1m01"] == ["1:00:18:00", "1:01:18:00", "1:00", "1:00:18:00"]
    # ... + 20 min of reel 1 + 5 s into reel 2.
    assert lines["2m01"] == ["1:20:13:00", "1:21:13:00", "1:00", "1:20:13:00"]
    # No place on the timeline: blank, with the duration as it was.
    assert lines["3m01"] == ["", "", "1:00", ""]
    assert lines["0m01"] == ["", "", "1:00", ""]
    assert log.notes == [
        "Long Play Time: reels end to end from 01:00:08:00, after a "
        "00:00:08:00 leader.",
        "Positions are blank for cues on No Reel Yet, Reel 3: no reel IN "
        "and OUT to place them by.",
    ]


def test_hour_zero_without_a_leader(project: models.Project) -> None:
    project.cues.filter(number__in=["0m01", "3m01"]).delete()
    cue = project.cues.get(number="1m01")
    cue.seq_start = HOUR + 24
    cue.save()
    log = cue_log.build(
        project,
        ["number", "seq_start", "tc_in", "start_offset"],
        long_play_time=long_play.Options(start_hour=0),
    )
    assert _lines(log)["1m01"] == ["0:00:01:00", "0:00:10:00", ""]
    assert _lines(log)["2m01"] == ["", "0:20:05:00", ""]
    assert log.notes == ["Long Play Time: reels end to end from 00:00:00:00."]


def test_without_it_positions_stay_per_reel(project: models.Project) -> None:
    log = cue_log.build(project, POSITIONS)
    assert _lines(log)["2m01"][0] == "2:00:05:00"
    assert not log.notes


def test_excel_and_pdf_carry_the_notes(
    project: models.Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = cue_log.build(project, POSITIONS, long_play_time=long_play.Options())
    book = openpyxl.load_workbook(io.BytesIO(cue_log.to_xlsx(log, TODAY)))
    sheet = book.active
    assert sheet is not None
    values = [
        [c for c in row if c is not None]
        for row in sheet.iter_rows(values_only=True)
    ]
    assert values[2] == [log.notes[0]] and values[3] == [log.notes[1]]
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        pdf, "render", lambda template, context: seen.update(context) or b""
    )
    cue_log.to_pdf(log, TODAY)
    assert seen["notes"] == log.notes


def test_the_pdf_renders_with_notes(project: models.Project) -> None:
    log = cue_log.build(project, ["number"], long_play_time=long_play.Options())
    body = cue_log.to_pdf(log, TODAY)
    assert body.startswith(b"%PDF")


# The dialog and download


def test_dialog_offers_it_except_for_long_play(
    viewer: Client, project: models.Project
) -> None:
    url = reverse("cues:export", args=[project.pk])
    html = viewer.get(url).content.decode()
    assert 'name="long_play"' in html and "Long Play Time" in html
    assert 'name="leader"' in html and 'name="start_hour"' in html
    assert "Time before the first reel" in html
    project.picture_type = models.Project.PictureType.LONG_PLAY
    project.save()
    html = viewer.get(url).content.decode()
    assert 'name="long_play"' not in html


def _download(client: Client, project: models.Project, **extra: str) -> Any:
    return client.get(
        reverse("cues:export_download", args=[project.pk]),
        {
            "format": "csv",
            "preset": "custom",
            "columns": ["number", "tc_in"],
            **extra,
        },
    )


def _csv(response: Any) -> list[list[str]]:
    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def test_download_with_long_play_time(
    viewer: Client, project: models.Project
) -> None:
    rows = _csv(
        _download(
            viewer,
            project,
            long_play="on",
            start_hour="0",
            leader="0:00:08:00",
        )
    )
    assert ["2", "2m01", "0:20:13:00"] in rows
    assert ["3", "3m01", ""] in rows
    # Off, the start and leader are ignored, even a leader that's wrong.
    rows = _csv(_download(viewer, project, start_hour="0", leader="nope"))
    assert ["2", "2m01", "2:00:05:00"] in rows


def test_a_bad_leader_says_so(viewer: Client, project: models.Project) -> None:
    response = _download(viewer, project, long_play="on", leader="8 seconds")
    assert response.status_code == 400
    assert b"Type the leader as a timecode" in response.content
    response = _download(viewer, project, long_play="on", start_hour="5")
    assert response.status_code == 400
    assert b"Those export choices aren't valid." in response.content


def test_long_play_projects_ignore_it(
    viewer: Client, project: models.Project
) -> None:
    project.picture_type = models.Project.PictureType.LONG_PLAY
    project.save()
    rows = _csv(_download(viewer, project, long_play="on", leader="x"))
    assert ["2", "2m01", "2:00:05:00"] in rows
