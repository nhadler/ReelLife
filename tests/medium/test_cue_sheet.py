"""The PRO cue sheet: canonical sheet, checklist, renderers."""

from collections.abc import Callable
import datetime
from decimal import Decimal
import io
from typing import Any
import warnings

from django.test import Client
from django.urls import reverse
import openpyxl
import pytest

from accounts.models import Role
from cues import models
from cues.exporters.cue_sheet import generic
from cues.exporters.cue_sheet import model
from cues.exporters.cue_sheet import rapidcue
from cues.services import shares
from tests import factories

pytestmark = pytest.mark.django_db

TODAY = datetime.date(2026, 10, 31)
SECOND = 24
HOUR = 3600 * SECOND


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    """Two scored cues with writers, an omitted cue, a licensed song."""
    project = factories.ProjectFactory.create(
        title="The Voyage",
        production_number="TV101",
        production_company="Northlight",
        cue_sheet_category="Movie",
        cue_sheet_version="Theatrical",
        preparer="Casey Composer",
        preparer_email="casey@example.com",
        company_address="1 Pier Road",
        release_date=datetime.date(2027, 3, 1),
    )
    picture = factories.PictureFactory.create(project=project, name="1")
    factories.PictureVersionFactory.create(picture=picture)
    writer = models.Party.objects.create(
        name="Casey J. Composer",
        kind="writer",
        society="BMI",
        ipi="00123456789",
    )
    publisher = models.Party.objects.create(
        name="Casey Music", kind="publisher", society="BMI"
    )
    to_write = project.statuses.get(label="To Write")

    def cue(number: str, start: int, **fields: Any) -> models.Cue:
        return factories.CueFactory.create(
            project=project,
            picture=picture,
            number=number,
            tc_in=HOUR + start * SECOND,
            tc_out=HOUR + (start + 30) * SECOND + 12,  # 30.5 s: rounds up
            status=fields.pop("status", to_write),
            **fields,
        )

    first = cue("1m01", 0, title="Main Title", usage_theme="MT")
    shares.set_shares(
        first,
        [
            shares.Line(writer, "C", Decimal(100)),
            shares.Line(publisher, "E", Decimal(100)),
        ],
    )
    second = cue(
        "1m02",
        60,
        title="=HYPERLINK(1)",
        plays_as_source=True,
        interrupted_use=True,
    )
    shares.set_shares(second, [shares.Line(writer, "C", Decimal(50))])
    cue("1m03", 120, title="Gone", status=project.statuses.get(kind="omit"))
    cue(
        "1m04",
        180,
        title="Radio Song",
        artist="The Band",
        status=project.statuses.get(kind="license"),
        cue_sheet_notes="From the album",
    )
    return project


def test_build(project: models.Project) -> None:
    sheet = model.build(project, model.Options(), TODAY)
    assert [line.number for line in sheet.lines] == ["1m01", "1m02", "1m04"]
    first = sheet.lines[0]
    assert (first.sequence, first.title, first.usage, first.seconds) == (
        1,
        "Main Title",
        "MT",
        31,
    )
    assert first.parties[0].first_name == "Casey J."
    assert first.parties[0].last_name == "Composer"
    assert first.parties[0].ipi == "00123456789"
    assert sheet.music_seconds == 93
    assert sheet.header.prepared_on == TODAY
    everything = model.build(
        project,
        model.Options(
            include_inactive=True,
            prefix_numbers=True,
            include_ipi=False,
            include_notes=True,
            include_artist=True,
        ),
        TODAY,
    )
    assert [line.number for line in everything.lines] == [
        "1m01",
        "1m02",
        "1m03",
        "1m04",
    ]
    assert everything.lines[0].title == "1m01 Main Title"
    assert everything.lines[0].parties[0].ipi == ""
    assert everything.lines[1].notes == "Source; Interrupted"
    song = everything.lines[3]
    assert song.notes == "Licensed; From the album"
    assert song.artist == "The Band"
    episode = factories.ProjectFactory.create(episode_number=4)
    assert model.build(episode, model.Options()).header.episode_number == "4"


def test_split_name() -> None:
    assert model.split_name("Casey  Composer") == ("Casey", "Composer")
    assert model.split_name("van der Berg, Ann") == ("Ann", "van der Berg")
    assert model.split_name("Cher") == ("", "Cher")


def test_checklist(project: models.Project) -> None:
    sheet = model.build(project, model.Options(), TODAY)
    issues = [(i.number, i.message) for i in model.checklist(sheet)]
    assert ("1m02", "Writer shares add up to 50%, not 100%.") in issues
    assert ("1m04", "Add at least one writer.") in issues
    assert (
        "1m01",
        "Still To Write: is it in the final picture as written?",
    ) in (issues)
    first = project.cues.get(number="1m01")
    first.status = project.statuses.filter(kind=models.StatusKind.DONE).first()
    first.save()
    sheet = model.build(project, model.Options(), TODAY)
    issues = [(i.number, i.message) for i in model.checklist(sheet)]
    assert not [i for i in issues if i[0] == "1m01"]
    reel = first.picture
    first.current_version = factories.CueVersionFactory.create(
        cue=first, label="v9", written_to=reel.current_version()
    )
    first.save()
    later = factories.TurnoverFactory.create(
        project=project, received_date=TODAY
    )
    factories.PictureVersionFactory.create(
        picture=reel, turnover=later, tc_in=0, tc_out=10_000_000
    )
    sheet = model.build(project, model.Options(), TODAY)
    issues = [(i.number, i.message) for i in model.checklist(sheet)]
    assert (
        "1m01",
        "Written to older picture: conform it, or check its timings against "
        "the final cut.",
    ) in issues
    bare = factories.ProjectFactory.create()
    picture = factories.PictureFactory.create(project=bare)
    factories.CueFactory.create(
        project=bare, picture=picture, title="", tc_out=None, usage="VD"
    )
    unnamed = models.Party.objects.create(name="Nobody", kind="writer")
    cue = bare.cues.get()
    shares.set_shares(cue, [shares.Line(unnamed, "C", Decimal(100))])
    messages = [
        i.message for i in model.checklist(model.build(bare, model.Options()))
    ]
    for expected in (
        "Set the production company in Project Settings, Cue Sheet.",
        "The program has no length: log the picture's reels.",
        "No title.",
        "No length: set IN and OUT.",
        "Usage VD isn't one RapidCue takes (BI, BV, VI, VV, MT, ET, Logo).",
        "Nobody has no PRO.",
    ):
        assert expected in messages
    empty = factories.ProjectFactory.create()
    assert "No cues to list." in [
        i.message for i in model.checklist(model.build(empty, model.Options()))
    ]


def test_generic_csv(project: models.Project) -> None:
    sheet = model.build(project, model.Options(), TODAY)
    text = generic.to_csv(sheet).decode("utf-8-sig")
    lines = text.splitlines()
    assert lines[0].startswith("Seq. #,Cue #,Title,Usage")
    assert lines[1] == (
        "1,1m01,Main Title,MT,1:00:00:00,1:00:30:12,0:31,C,Casey J. Composer,"
        "Writer,BMI,00123456789,100,,"
    )
    assert lines[2].startswith("1,1m01,Main Title,MT,")
    assert ",E,Casey Music,Publisher,BMI,,100" in lines[2]
    assert lines[3].startswith("2,1m02,'=HYPERLINK(1),")  # never a formula
    assert lines[4].startswith("3,1m04,Radio Song,BI,")
    assert lines[4].endswith(",,,,,,,,")


def _book(data: bytes) -> Any:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return openpyxl.load_workbook(io.BytesIO(data))["Template"]


def test_rapidcue(project: models.Project) -> None:
    sheet = model.build(project, model.Options(revision=True), TODAY)
    cells = _book(rapidcue.to_xlsx(sheet))
    assert cells["D4"].value == "Revision"
    assert cells["D9"].value == "Movie"
    assert cells["N4"].value == "The Voyage"
    assert cells["N9"].value == "TV101"
    assert cells["N13"].value == "Casey Composer"
    # Rows: 1m01 composer, 1m01 publisher, 1m02 composer, 1m04 (no party).
    row = [cells[f"{c}20"].value for c in "ABCDEFGHIJKLMNOPQR"]
    assert row == [
        1, "Main Title", "MT", 1, 0, 0, 1, 0, 30, 0, 31,
        "Composer", "Casey J.", "Composer", None, "00123456789", "BMI", 1.0,
    ]  # fmt: skip
    assert cells["P20"].data_type == "s"  # the IPI keeps its zeros
    publisher = [cells[f"{c}21"].value for c in "LMNOPQR"]
    assert publisher == [
        "Publisher",
        None,
        None,
        "Casey Music",
        None,
        "BMI",
        1.0,
    ]
    assert str(cells["A21"].value).startswith("=")  # the template's own
    assert cells["B22"].value == "=HYPERLINK(1)"
    assert cells["B22"].data_type == "s"  # text, not a formula
    assert cells["R22"].value == 0.5
    assert cells["B23"].value == "Radio Song"
    assert cells["L23"].value is None
    assert cells["D4"].value == "Revision"
    original = model.build(project, model.Options(), TODAY)
    assert _book(rapidcue.to_xlsx(original))["D4"].value == "Original"
    assert (
        rapidcue.filename(sheet, TODAY)
        == "TV101 The Voyage cue sheet 2026-10-31.xlsx"
    )
    untitled = factories.ProjectFactory.create(
        title="???", production_number=""
    )
    assert (
        rapidcue.filename(model.build(untitled, model.Options()), TODAY)
        == "cue sheet cue sheet 2026-10-31.xlsx"
    )


def test_rapidcue_grows_the_table_past_the_template(
    project: models.Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rapidcue, "LAST_TEMPLATE_ROW", 21)
    sheet = model.build(project, model.Options(), TODAY)
    cells = _book(rapidcue.to_xlsx(sheet))
    assert cells.tables["CueDetails2"].ref == "A20:R23"


def test_a_cue_without_times(project: models.Project) -> None:
    cue = project.cues.get(number="1m01")
    cue.tc_in = None
    cue.save()
    sheet = model.build(project, model.Options(), TODAY)
    cells = _book(rapidcue.to_xlsx(sheet))
    assert cells["D20"].value is None
    assert cells["G20"].value == 1


@pytest.fixture(name="viewer")
def fixture_viewer(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.VIEWER)


def test_dialog_checklist_and_downloads(
    viewer: Client, project: models.Project
) -> None:
    html = viewer.get(reverse("cues:cue_sheet", args=[project.pk])).content
    page = html.decode()
    assert "Export Cue Sheet" in page
    assert "3 cues:" in page
    assert "Writer shares add up to 50%" in page
    checklist = reverse("cues:cue_sheet_checklist", args=[project.pk])
    part = viewer.get(
        checklist, {"format": "csv", "include_inactive": "on"}
    ).content.decode()
    assert "4 cues:" in part
    assert viewer.get(checklist, {"format": "nope"}).status_code == 400
    download = reverse("cues:cue_sheet_download", args=[project.pk])
    xlsx = viewer.get(download, {"format": "rapidcue", "include_ipi": "on"})
    assert xlsx["Content-Type"].startswith("application/vnd.openxml")
    assert "TV101 The Voyage cue sheet" in xlsx["Content-Disposition"]
    assert _book(xlsx.content)["N4"].value == "The Voyage"
    csv = viewer.get(download, {"format": "csv"})
    assert csv["Content-Type"].startswith("text/csv")
    assert ".csv" in csv["Content-Disposition"]
    assert viewer.get(download, {"format": "pdf"}).status_code == 400


def test_ready_to_send(viewer: Client) -> None:
    project = factories.ProjectFactory.create(
        production_company="Northlight",
        cue_sheet_category="Movie",
        preparer="Casey",
    )
    picture = factories.PictureFactory.create(project=project)
    factories.PictureVersionFactory.create(picture=picture)
    cue = factories.CueFactory.create(project=project, picture=picture)
    writer = models.Party.objects.create(
        name="Casey", kind="writer", society="BMI"
    )
    shares.set_shares(cue, [shares.Line(writer, "CA", Decimal(100))])
    page = viewer.get(reverse("cues:cue_sheet", args=[project.pk])).content
    assert b"ready to send." in page


def test_menu_and_settings(
    role_client: Callable[[Role], Client], project: models.Project
) -> None:
    editor = role_client(Role.EDITOR)
    page = editor.get(reverse("cues:project", args=[project.pk])).content
    assert b'id="grid-cue-sheet"' in page
    url = reverse("cues:settings_cue_sheet", args=[project.pk])
    data = {
        "numbering": "standard",
        "aka": "Lighthouse",
        "cue_sheet_category": "TV Movie",
        "cue_sheet_version": "Cable",
        "preparer_email": "not an email",
        "defaults-TOTAL_FORMS": "0",
        "defaults-INITIAL_FORMS": "0",
        "defaults-MIN_NUM_FORMS": "0",
        "defaults-MAX_NUM_FORMS": "1000",
    }
    assert b"Enter a valid email address." in editor.post(url, data).content
    data["preparer_email"] = "casey@example.com"
    assert editor.post(url, data)["Location"] == url
    project.refresh_from_db()
    assert (project.aka, project.cue_sheet_category) == (
        "Lighthouse",
        "TV Movie",
    )
