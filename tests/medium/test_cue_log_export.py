"""The cue log export and its dialog (7.8)."""

from collections.abc import Callable
import csv
import datetime
import io

from django.test import Client
from django.urls import reverse
import openpyxl
import pytest

from accounts.models import Role
from cues import models
from cues.exporters import cue_log
from cues.services import demo
from tests import factories

pytestmark = pytest.mark.django_db

TODAY = datetime.date(2026, 10, 30)


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    return demo.build_demo().project


@pytest.fixture(name="viewer")
def fixture_viewer(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.VIEWER)


def _rows(text: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text.decode("utf-8-sig"))))


@pytest.mark.parametrize(
    ("text", "safe"),
    [
        ('=HYPERLINK("http://x")', '\'=HYPERLINK("http://x")'),
        ("+cmd|' /C calc'!A0", "'+cmd|' /C calc'!A0"),
        ("-2+3", "'-2+3"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tTab", "'\tTab"),
        ("-00:00:02:12", "-00:00:02:12"),
        ("+00:00:02:12", "+00:00:02:12"),
        ("-12.5", "-12.5"),
        ("Main Title", "Main Title"),
        ("", ""),
    ],
)
def test_csv_safe(text: str, safe: str) -> None:
    assert cue_log.csv_safe(text) == safe


def test_build_reads_like_the_grid(project: models.Project) -> None:
    cue = project.cues.get(number="1m03")
    current = cue.current_version
    assert current is not None
    current.sub_status = project.sub_statuses.get(label="Reuse")
    current.save()
    cue.plays_as_source = True
    cue.save()
    log = cue_log.build(
        project,
        [
            "number",
            "title",
            "status",
            "sub_status",
            "tc_in",
            "duration",
            "written_to",
            "usage",
            "plays_as_source",
            "nope",
        ],
    )
    assert log.headers == [
        "Cue #",
        "Title",
        "Status",
        "Sub Status",
        "IN",
        "DUR",
        "Written to Picture",
        "Usage",
        "Plays as Source",
    ]
    assert [reel.name for reel in log.reels] == ["1", "2", "3"]
    row = next(r for r in log.reels[0].rows if r[0] == "1m03")
    assert row[2] == "Composed"
    assert row[3] == "Reuse"
    assert row[6] == "Reel 1 - c0917"
    assert row[7] == "Background Instrumental"
    assert row[8] == "Yes"
    assert log.reels[0].cues == 14
    assert log.reels[0].frames > 0


def test_only_the_given_cues_in_their_order(project: models.Project) -> None:
    second, first = (
        project.cues.get(number="2m01"),
        project.cues.get(number="1m02"),
    )
    log = cue_log.build(project, ["number"], cue_ids=[second.pk, first.pk, 0])
    assert [(r.name, r.rows) for r in log.reels] == [
        ("2", [["2m01"]]),
        ("1", [["1m02"]]),
    ]


def test_empty_columns_can_be_left_out(project: models.Project) -> None:
    log = cue_log.build(project, ["number", "artist"], exclude_empty=True)
    assert log.headers == ["Cue #"]


def test_totals_and_progress(project: models.Project) -> None:
    log = cue_log.build(project, ["number"], totals=True, show_progress=True)
    names = [line[0] for line in log.totals]
    assert names == [
        "Music totals",
        "Compositions",
        "Songs",
        "Licensed",
        "Total",
        "Runtime",
    ]
    assert log.totals[1] == ["Compositions", "36", "1:03:36", "93%"]
    assert (log.totals[2][1], log.totals[3][1], log.totals[4][1]) == (
        "1",
        "2",
        "39",
    )
    assert log.progress[1][0].startswith("Composed 23 / 36 cues")


def test_totals_of_an_empty_project() -> None:
    log = cue_log.build(
        factories.ProjectFactory.create(), ["number"], totals=True
    )
    assert log.totals[1] == ["Compositions", "0", "0:00", "0%"]
    assert not log.reels


def test_csv(project: models.Project) -> None:
    cue = project.cues.get(number="1m01")
    cue.title = "=cmd()"
    cue.save()
    data = cue_log.to_csv(
        cue_log.build(project, ["number", "title", "start_offset"])
    )
    assert data.startswith("﻿".encode())
    rows = _rows(data)
    assert rows[0] == ["Reel", "Cue #", "Title", "Start Offset"]
    assert rows[1] == ["1", "1m01", "'=cmd()", ""]
    stale = next(r for r in rows if r[1] == "2m04")
    assert stale[3] == "+00:00:02:12"


def test_xlsx(project: models.Project) -> None:
    cue = project.cues.get(number="1m01")
    cue.title = "=cmd()"
    cue.save()
    log = cue_log.build(
        project, ["number", "title"], totals=True, show_progress=True
    )
    book = openpyxl.load_workbook(io.BytesIO(cue_log.to_xlsx(log, TODAY)))
    sheet = book.active
    assert sheet is not None
    values = [
        [v for v in (c.value for c in row) if v is not None]
        for row in sheet.iter_rows()
    ]
    assert values[0] == ["DEMO101 The Lighthouse Keeper: cue log"]
    assert values[1] == ["Exported 30 Oct 2026, 23.976"]
    assert ["Music totals", "Cues", "Duration", "Share"] in values
    assert ["Cue #", "Title"] in values
    assert ["Reel 1"] in values
    cells = [c for row in sheet.iter_rows() for c in row if c.value == "=cmd()"]
    assert cells and all(c.data_type == "s" for c in cells)
    assert any(v and str(v[0]).startswith("Reel 1: 14 cues,") for v in values)


def test_filename() -> None:
    project = factories.ProjectFactory.create(
        production_number="AB1", title='Who/What: "Now"?'
    )
    assert cue_log.filename(project, TODAY, ".csv") == (
        "AB1 Who-What- -Now-- Cue Log 2026-10-30.csv"
    )


# The dialog and download


def test_dialog_for_viewers_too(
    viewer: Client, project: models.Project
) -> None:
    view = models.View.objects.filter(project=None).order_by("order").first()
    assert view is not None
    first = project.cues.get(number="1m01")
    url = reverse("cues:export", args=[project.pk])
    html = viewer.get(f"{url}?view={view.pk}&ids={first.pk},x").content.decode()
    assert "Export Cues" in html
    assert f'value="{view.pk}"' in html and "checked" in html
    assert "The 1 shown in the grid" in html
    assert 'value="shown" checked' in html
    html = viewer.get(url).content.decode()
    assert "shown in the grid" not in html


def test_download_xlsx_from_a_view(
    viewer: Client, project: models.Project
) -> None:
    view = models.View.objects.filter(project=None).order_by("order").first()
    assert view is not None
    response = viewer.get(
        reverse("cues:export_download", args=[project.pk]),
        {
            "format": "xlsx",
            "preset": str(view.pk),
            "scope": "all",
            "totals": "on",
        },
    )
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/vnd.openxmlformats")
    assert (
        'filename="DEMO101 The Lighthouse Keeper Cue Log'
        in response["Content-Disposition"]
    )
    assert openpyxl.load_workbook(io.BytesIO(response.content))


def test_download_csv_of_shown_cues(
    viewer: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="3m02")
    response = viewer.get(
        reverse("cues:export_download", args=[project.pk]),
        {
            "format": "csv",
            "preset": "custom",
            "columns": ["number", "title"],
            "scope": "shown",
            "ids": str(cue.pk),
        },
    )
    assert _rows(response.content) == [
        ["Reel", "Cue #", "Title"],
        ["3", "3m02", cue.title],
    ]


def test_bad_downloads(viewer: Client, project: models.Project) -> None:
    url = reverse("cues:export_download", args=[project.pk])
    assert (
        viewer.get(url, {"format": "doc", "preset": "custom"}).status_code
        == 400
    )
    response = viewer.get(url, {"format": "csv", "preset": "custom"})
    assert response.status_code == 400
    assert b"Choose at least one column" in response.content


def test_project_page_has_export(
    viewer: Client, project: models.Project
) -> None:
    html = viewer.get(
        reverse("cues:project", args=[project.pk])
    ).content.decode()
    assert 'id="grid-export"' in html
    assert f'"exportUrl": "/projects/{project.pk}/export/"' in html


def test_held_cues_are_left_out_of_totals(project: models.Project) -> None:
    held = project.statuses.filter(kind=models.StatusKind.ON_HOLD).first()
    assert held is not None
    project.cues.filter(number="1m01").update(status=held)
    log = cue_log.build(project, ["number"], totals=True)
    assert log.totals[1][1] == "35"
