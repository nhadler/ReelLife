"""Writers, publishers and shares."""

from collections.abc import Callable
import csv
from decimal import Decimal
from html import unescape
import importlib
import io
from typing import Any

from django.apps import apps as django_apps
from django.test import Client
from django.urls import reverse
import openpyxl
import pytest

from accounts.models import Role
from cues import models
from cues.services import authoring
from cues.services import history
from cues.services import shares
from cues.services import tools
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


@pytest.fixture(name="writer")
def fixture_writer() -> models.Party:
    return models.Party.objects.create(
        first_name="Casey",
        last_name="Composer",
        kind=models.Party.Kind.WRITER,
        society="BMI",
        ipi="00123456789",
        is_mine=True,
    )


@pytest.fixture(name="publisher")
def fixture_publisher() -> models.Party:
    return models.Party.objects.create(
        name="Casey Music", kind=models.Party.Kind.PUBLISHER, society="BMI"
    )


def _line(party: models.Party, role: str, share: str) -> shares.Line:
    return shares.Line(party, role, Decimal(share))


def test_problems(writer: models.Party, publisher: models.Party) -> None:
    assert not shares.problems([])
    assert not shares.problems(
        [_line(writer, "C", "100"), _line(publisher, "E", "100")]
    )
    assert shares.problems([_line(writer, "C", "60")]) == [
        "Writer shares add up to 60%, not 100%."
    ]
    assert shares.problems(
        [_line(writer, "C", "100"), _line(publisher, "E", "50")]
    ) == ["Publisher shares add up to 50%, not 100%."]
    assert shares.problems([_line(publisher, "E", "100")]) == [
        "Add at least one writer."
    ]
    assert shares.problems(
        [_line(writer, "E", "100"), _line(publisher, "C", "100")]
    ) == [
        "Casey Composer isn't a publisher, so can't be E.",
        "Casey Music isn't a writer, so can't be C.",
    ]
    assert str(writer) == "Casey Composer (BMI)"
    plain = models.Party(name="Anon", kind="writer")
    assert str(plain) == "Anon"


def _defaults(
    project: models.Project, *parties: tuple[models.Party, str]
) -> None:
    for order, (party, role) in enumerate(parties):
        models.DefaultShare.objects.create(
            project=project, party=party, role=role, share=100, order=order
        )


def test_new_compositions_get_the_defaults(
    writer: models.Party, publisher: models.Party
) -> None:
    project = factories.ProjectFactory.create()
    _defaults(project, (writer, "C"), (publisher, "E"))
    picture = factories.PictureFactory.create(project=project)
    cue = authoring.create_cue(
        models.Cue(
            project=project,
            picture=picture,
            number="1m01",
            status=project.statuses.get(label="To Write"),
        )
    )
    cue_shares = models.CueShare.objects.filter(cue=cue)
    assert [str(s) for s in cue_shares] == [
        "Casey Composer C 100%",
        "Casey Music E 100%",
    ]
    assert cue_shares[0].is_writer
    song = authoring.create_cue(
        models.Cue(
            project=project,
            picture=picture,
            number="1m02",
            status=project.statuses.get(kind=models.StatusKind.SONG),
        )
    )
    assert not song.shares.exists()
    # Undoing the new cue takes its shares too.
    history.undo(models.ChangeSet.objects.filter(project=project).latest("pk"))
    history.undo(
        models.ChangeSet.objects.filter(project=project, undone_at=None)
        .exclude(undo_of__isnull=False)
        .latest("pk")
    )
    assert not models.CueShare.objects.filter(cue__project=project).exists()


def test_split_and_copy_carry_the_shares(
    writer: models.Party, publisher: models.Party
) -> None:
    project = factories.ProjectFactory.create()
    _defaults(project, (writer, "C"))
    reel = factories.PictureFactory.create(project=project, name="1")
    cue = factories.CueFactory.create(
        project=project, picture=reel, number="1m01"
    )
    shares.set_shares(
        cue, [_line(publisher, "E", "100"), _line(writer, "CA", "100")]
    )
    assert cue.tc_in is not None
    plan = tools.plan_split(cue, cue.tc_in + 24, "1m01a")
    new = tools.split(plan)
    assert [s.role for s in new.shares.all()] == ["E", "CA"]
    other = factories.PictureFactory.create(project=project, name="2")
    copy = tools.copy([cue], other)[0]
    assert [s.role for s in copy.shares.all()] == ["E", "CA"]
    bare = factories.CueFactory.create(
        project=project, picture=reel, number="1m05"
    )
    copy = tools.copy([bare], other)[0]
    assert not copy.shares.exists()


def test_apply_defaults(writer: models.Party) -> None:
    project = factories.ProjectFactory.create()
    picture = factories.PictureFactory.create(project=project)
    empty = factories.CueFactory.create(project=project, picture=picture)
    shared = factories.CueFactory.create(project=project, picture=picture)
    shares.set_shares(shared, [_line(writer, "A", "100")])
    song = factories.CueFactory.create(
        project=project,
        picture=picture,
        status=project.statuses.get(kind=models.StatusKind.SONG),
    )
    assert shares.apply_defaults(project) == 0  # no defaults yet
    _defaults(project, (writer, "C"))
    assert shares.apply_defaults(project) == 1
    assert [s.role for s in empty.shares.all()] == ["C"]
    assert [s.role for s in shared.shares.all()] == ["A"]
    assert shares.apply_defaults(project, replace=True) == 2
    assert [s.role for s in shared.shares.all()] == ["C"]
    assert not song.shares.exists()
    history.undo(models.ChangeSet.objects.filter(project=project).latest("pk"))
    assert [s.role for s in shared.shares.all()] == ["A"]


def _management(prefix: str, total: int, initial: int) -> dict[str, str]:
    return {
        f"{prefix}-TOTAL_FORMS": str(total),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }


def test_directory_page(
    editor: Client,
    writer: models.Party,
    role_client: Callable[[Role], Client],
) -> None:
    url = reverse("cues:credits")
    html = editor.get(url).content.decode()
    assert 'value="Casey"' in html and 'value="Composer"' in html
    assert 'id="writers-title">Writers' in html
    assert 'id="publishers-title">Publishers' in html
    assert '<datalist id="societies">' in html
    assert "Not yet" in html  # Casey holds no shares yet
    data = {
        **_management("writers", 2, 1),
        **_management("publishers", 1, 0),
        "writers-0-id": str(writer.pk),
        "writers-0-first_name": "Casey",
        "writers-0-last_name": "Composer",
        "writers-0-society": "BMI",
        "writers-0-ipi": "123",
        "writers-1-first_name": "Lyric",
        "writers-1-last_name": "Person",
        "publishers-0-name": "Harbor Music",
        "publishers-0-society": "BMI",
    }
    page = editor.post(url, data).content.decode()
    assert "9 to 11 digits" in page
    data["writers-0-ipi"] = "00123456789"
    assert editor.post(url, data)["Location"] == url
    lyric = models.Party.objects.get(last_name="Person")
    assert (lyric.name, lyric.first_name, lyric.kind) == (
        "Lyric Person",
        "Lyric",
        models.Party.Kind.WRITER,
    )
    harbor = models.Party.objects.get(name="Harbor Music")
    assert harbor.kind == models.Party.Kind.PUBLISHER
    viewer_page = role_client(Role.VIEWER).get(url).content.decode()
    assert (
        "Casey" in viewer_page
        and "Harbor Music" in viewer_page
        and "<input" not in viewer_page.split("<main")[1].split("</main>")[0]
    )


def test_writers_need_a_last_name_and_publishers_a_name(
    editor: Client,
) -> None:
    url = reverse("cues:credits")
    data = {
        **_management("writers", 1, 0),
        **_management("publishers", 1, 0),
        "writers-0-first_name": "Cher",
        "publishers-0-society": "BMI",
    }
    page = editor.post(url, data).content.decode()
    assert "A writer needs a last name." in page
    assert "A publisher needs a name." in page
    assert not models.Party.objects.exists()


def test_a_party_in_use_cannot_be_deleted(
    editor: Client, writer: models.Party
) -> None:
    cue = factories.CueFactory.create()
    shares.set_shares(cue, [_line(writer, "C", "100")])
    url = reverse("cues:credits")
    data: dict[str, Any] = {
        **_management("writers", 1, 1),
        **_management("publishers", 0, 0),
        "writers-0-id": str(writer.pk),
        "writers-0-first_name": "Casey",
        "writers-0-last_name": "Composer",
        "writers-0-DELETE": "on",
    }
    assert "has shares in cues" in editor.post(url, data).content.decode()
    assert models.Party.objects.filter(pk=writer.pk).exists()


def _share_data(prefix: str, *rows: dict[str, str]) -> dict[str, str]:
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(rows)),
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for i, row in enumerate(rows):
        data |= {f"{prefix}-{i}-{key}": value for key, value in row.items()}
    return data


def test_cue_page_shares(
    editor: Client, writer: models.Party, publisher: models.Party
) -> None:
    cue = factories.CueFactory.create()
    page = editor.get(reverse("cues:cue", args=[cue.pk])).content.decode()
    assert "Credits" in page
    url = reverse("cues:cue_shares", args=[cue.pk])
    bad = _share_data(
        "shares",
        {"party": str(writer.pk), "role": "C", "share": "50", "order": "0"},
    )
    bad["shares_seen"] = shares.revision(cue)
    html = editor.post(url, bad).content.decode()
    assert "Writer shares add up to 50%" in html
    good = _share_data(
        "shares",
        {"party": str(writer.pk), "role": "C", "share": "100", "order": "0"},
        {"party": str(publisher.pk), "role": "E", "share": "100", "order": "1"},
        {"party": "", "role": "", "share": "", "order": "0"},
    )
    good["shares_seen"] = shares.revision(cue)
    html = editor.post(url, good).content.decode()
    assert "Saved." in html
    assert [s.role for s in cue.shares.all()] == ["C", "E"]
    change = models.ChangeSet.objects.filter(project=cue.project).latest("pk")
    assert change.label == f"Edit {cue.number} Shares"
    bare = factories.CueFactory.create()
    shares.set_shares(bare, [_line(publisher, "E", "100")])
    page = editor.get(reverse("cues:cue", args=[bare.pk])).content.decode()
    assert "Add at least one writer." in page


def test_a_save_from_a_stale_page_is_refused(
    editor: Client, writer: models.Party, publisher: models.Party
) -> None:
    cue = factories.CueFactory.create()
    url = reverse("cues:cue_shares", args=[cue.pk])
    seen = shares.revision(cue)  # both editors open the empty section
    shares.set_shares(cue, [_line(writer, "C", "100")])  # one saves
    mine = _share_data(
        "shares",
        {"party": str(publisher.pk), "role": "E", "share": "100", "order": "0"},
        {"party": str(writer.pk), "role": "CA", "share": "100", "order": "1"},
        {"party": str(writer.pk), "role": "C", "share": "1", "DELETE": "on"},
    ) | {"shares_seen": seen}
    html = unescape(editor.post(url, mine).content.decode())
    assert "Someone else changed these credits" in html
    assert (
        "You had: Casey Music (BMI) E 100%; Casey Composer (BMI) CA 100%."
        in html
    )
    assert [str(s) for s in cue.shares.all()] == ["Casey Composer C 100%"]
    assert f'value="{shares.revision(cue)}"' in html
    garbled = {"shares-TOTAL_FORMS": "lots", "shares_seen": seen}
    html = editor.post(url, garbled).content.decode()
    assert "Someone else changed these credits" in html
    assert "You had" not in html


@pytest.mark.parametrize(
    ("cue_type", "expected"),
    [("SCORE", True), ("SONG", False), ("SOURCE", False)],
)
def test_songs_and_source_never_count_as_compositions(
    cue_type: str, expected: bool
) -> None:
    cue = factories.CueFactory.create(cue_type=cue_type)
    cue.status = cue.project.statuses.filter(
        kind=models.StatusKind.TO_DO
    ).first()
    assert shares.is_composition(cue) is expected


def test_viewers_see_shares_read_only(
    role_client: Callable[[Role], Client], writer: models.Party
) -> None:
    cue = factories.CueFactory.create()
    shares.set_shares(cue, [_line(writer, "C", "100")])
    viewer = role_client(Role.VIEWER)
    page = viewer.get(reverse("cues:cue", args=[cue.pk])).content.decode()
    assert "Casey Composer C 100%" in page
    empty = factories.CueFactory.create()
    page = viewer.get(reverse("cues:cue", args=[empty.pk])).content.decode()
    assert "No writers or publishers yet." in page
    response = viewer.post(reverse("cues:cue_shares", args=[cue.pk]), {})
    assert response.status_code == 403


def test_cue_sheet_settings_defaults_and_apply(
    editor: Client, writer: models.Party
) -> None:
    project = factories.ProjectFactory.create()
    factories.CueFactory.create(project=project)
    url = reverse("cues:settings_cue_sheet", args=[project.pk])
    page = editor.get(url).content.decode()
    assert "Default Shares" in page
    assert "Apply Default Shares" not in page
    data = {
        "numbering": "standard",
        **_share_data(
            "defaults",
            {
                "party": str(writer.pk),
                "role": "C",
                "share": "100",
                "order": "0",
            },
        ),
    }
    assert editor.post(url, data)["Location"] == url
    assert project.default_shares.count() == 1
    apply = reverse("cues:apply_default_shares", args=[project.pk])
    page = editor.post(apply, {}, follow=True).content.decode()
    assert "Gave 1 cue the default shares." in page
    page = editor.post(apply, {}, follow=True).content.decode()
    assert "No cues needed the default shares." in page
    page = editor.post(apply, {"replace": "on"}, follow=True).content.decode()
    assert "Gave 1 cue the default shares." in page


def test_more_share_edges(
    editor: Client, writer: models.Party, publisher: models.Party
) -> None:
    cue = factories.CueFactory.create()
    shares.set_shares(cue, [_line(writer, "C", "100")])
    shares.set_shares(cue, [_line(writer, "CA", "100")])  # replaces
    assert [s.role for s in cue.shares.all()] == ["CA"]
    url = reverse("cues:cue_shares", args=[cue.pk])
    bad = _share_data(
        "shares",
        {"party": str(writer.pk), "role": "C", "share": "lots", "order": "0"},
    )
    bad["shares_seen"] = shares.revision(cue)
    assert "Enter a number." in editor.post(url, bad).content.decode()
    # An unused publisher can be deleted.
    directory = reverse("cues:credits")
    data = {
        **_management("writers", 0, 0),
        **_management("publishers", 1, 1),
        "publishers-0-id": str(publisher.pk),
        "publishers-0-name": publisher.name,
        "publishers-0-DELETE": "on",
    }
    assert editor.post(directory, data)["Location"] == directory
    assert not models.Party.objects.filter(pk=publisher.pk).exists()


def test_grid_columns(
    editor: Client, writer: models.Party, publisher: models.Party
) -> None:
    cue = factories.CueFactory.create()
    shares.set_shares(
        cue,
        [
            _line(writer, "C", "50"),
            _line(
                models.Party.objects.create(name="Lyr", kind="writer"),
                "A",
                "50",
            ),
            _line(publisher, "E", "100"),
        ],
    )
    data = editor.get(reverse("cues:api_grid", args=[cue.project.pk])).json()
    row = data["rows"][0]
    assert row["writers"] == "Casey Composer (C 50%); Lyr (A 50%)"
    assert row["publishers"] == "Casey Music (E 100%)"
    cue_sheet = next(v for v in data["views"] if v["name"] == "Cue Sheet")
    assert {"writers", "publishers"} <= {c["id"] for c in cue_sheet["columns"]}


def test_series_defaults(
    editor: Client, writer: models.Party, publisher: models.Party
) -> None:
    series = models.Series.objects.create(title="Harbour Lights")
    episode = factories.ProjectFactory.create(kind="episode", series=series)
    models.SeriesShare.objects.create(
        series=series, party=writer, role="C", share=100
    )
    picture = factories.PictureFactory.create(project=episode)
    first = authoring.create_cue(
        models.Cue(project=episode, picture=picture, number="1m01")
    )
    assert [s.role for s in models.CueShare.objects.filter(cue=first)] == ["C"]
    # The episode's own defaults win over the series'.
    _defaults(episode, (publisher, "E"))
    second = authoring.create_cue(
        models.Cue(project=episode, picture=picture, number="1m02")
    )
    assert [s.role for s in models.CueShare.objects.filter(cue=second)] == ["E"]
    url = reverse("cues:settings_cue_sheet", args=[episode.pk])
    page = editor.get(url).content.decode()
    assert "Series Default Shares" in page and "Harbour Lights" in page
    data = {
        "numbering": "standard",
        **_share_data("defaults"),
        **_share_data(
            "series",
            {
                "party": str(writer.pk),
                "role": "CA",
                "share": "100",
                "order": "0",
            },
        ),
    }
    assert editor.post(url, data)["Location"] == url
    assert [s.role for s in series.default_shares.all()] == ["C", "CA"]
    # A writer in series defaults can't be deleted from the directory.
    directory = reverse("cues:credits")
    models.CueShare.objects.all().delete()
    models.DefaultShare.objects.all().delete()
    remove = {
        **_management("writers", 1, 1),
        **_management("publishers", 0, 0),
        "writers-0-id": str(writer.pk),
        "writers-0-first_name": writer.first_name,
        "writers-0-last_name": writer.last_name,
        "writers-0-DELETE": "on",
    }
    assert "has shares" in editor.post(directory, remove).content.decode()


def test_existing_writers_are_split_into_first_and_last_names() -> None:
    migration = importlib.import_module(
        "cues.migrations.0036_party_first_last_name"
    )
    models.Party.objects.create(name="Casey J.  Composer", kind="writer")
    models.Party.objects.create(name="van der Berg, Ann", kind="writer")
    models.Party.objects.create(name="Cher", kind="writer")
    models.Party.objects.create(name="Big Music, Inc.", kind="publisher")
    migration.split_writers(django_apps, None)
    assert sorted(
        models.Party.objects.values_list("name", "first_name", "last_name")
    ) == [
        ("Ann van der Berg", "Ann", "van der Berg"),
        ("Big Music, Inc.", "", ""),
        ("Casey J. Composer", "Casey J.", "Composer"),
        ("Cher", "", "Cher"),
    ]


def test_credits_shows_where_each_party_is_used(
    editor: Client, writer: models.Party, publisher: models.Party
) -> None:
    cue = factories.CueFactory.create()
    shares.set_shares(cue, [_line(writer, "C", "100")])
    project = factories.ProjectFactory.create()
    models.DefaultShare.objects.create(
        project=project, party=publisher, role="E", share=100
    )
    html = editor.get(reverse("cues:credits")).content.decode()
    assert ">1 cue<" in html
    assert "1 default" in html
    assert "remove those shares first" in html


def test_credits_export(
    role_client: Callable[[Role], Client],
    writer: models.Party,
    publisher: models.Party,
) -> None:
    del publisher  # Casey Music, listed after the writers
    models.Party.objects.create(
        name="=HYPERLINK()", kind=models.Party.Kind.PUBLISHER
    )
    cue = factories.CueFactory.create()
    shares.set_shares(cue, [_line(writer, "C", "100")])
    viewer = role_client(Role.VIEWER)
    response = viewer.get(reverse("cues:credits_export", args=["csv"]))
    assert response["Content-Type"].startswith("text/csv")
    assert 'filename="ReelLife Credits ' in response["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows[0][:4] == ["Kind", "First Name", "Last Name", "Company Name"]
    assert rows[1] == [
        "Writer",
        "Casey",
        "Composer",
        "",
        "BMI",
        "00123456789",
        "Yes",
        "1",
    ]
    assert rows[2][3] == "'=HYPERLINK()"  # never a formula
    assert rows[3][3] == "Casey Music"
    book = viewer.get(reverse("cues:credits_export", args=["xlsx"]))
    sheet = openpyxl.load_workbook(io.BytesIO(book.content)).worksheets[0]
    assert sheet["C2"].value == "Composer"
    assert sheet["D3"].data_type == "s"
    missing = viewer.get(reverse("cues:credits_export", args=["pdf"]))
    assert missing.status_code == 404
    page = viewer.get(reverse("cues:credits")).content.decode()
    assert ">Excel</a>" in page
