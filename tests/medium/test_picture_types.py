"""Picture types do what they say (Nick, Nov 1).

Reels and acts are numbered, clips are letters and numbers, and long
play is one picture with no name; the app's words follow the type.
"""

from collections.abc import Callable
import datetime

from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from cues import models
from cues import transfer_forms
from cues.api import grid
from cues.services import projects as project_service
from cues.services import turnovers
from tests import factories

pytestmark = pytest.mark.django_db

HOUR = 86_400  # frames at 24 fps


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


def _log(project: models.Project, *names: str) -> models.Turnover:
    return turnovers.create_turnover(
        project,
        label=f"c{project.turnovers.count()}",
        received_date=datetime.date(2026, 10, 30),
        reels=[
            turnovers.ReelEntry(
                None, name, HOUR * (i + 1), HOUR * (i + 1) + 100
            )
            for i, name in enumerate(names)
        ],
    )


@pytest.mark.parametrize(
    ("kind", "good", "bad", "message"),
    [
        ("reels", "2", "2a", "Reel names are numbers"),
        ("acts", "3", "Three", "Act names are numbers"),
        ("clips", "A12", "A-12", "Clip names are letters and numbers"),
    ],
)
def test_names_fit_the_picture_type(
    kind: str, good: str, bad: str, message: str
) -> None:
    project = factories.ProjectFactory.create(
        picture_type=kind, frame_rate="24"
    )
    _log(project, good)
    with pytest.raises(ValidationError, match=message):
        _log(project, bad)


def test_long_play_has_one_picture() -> None:
    project = factories.ProjectFactory.create(
        picture_type="long_play", frame_rate="24"
    )
    project_service.placeholder_picture(project)  # N/A doesn't count
    _log(project, "1")
    with pytest.raises(ValidationError, match="one picture"):
        _log(project, "2")
    picture = project.pictures.get(name="1")
    assert (picture.title, project.picture_title("N/A")) == (
        "Picture",
        "No Picture Yet",
    )
    titles = [
        p["title"] for p in grid.pictures(project, grid.Context.build(project))
    ]
    assert titles == ["No Picture Yet", "Picture"]


def test_settings_refuse_a_type_the_pictures_dont_fit(editor: Client) -> None:
    project = factories.ProjectFactory.create(
        picture_type="clips", frame_rate="24"
    )
    _log(project, "A1", "B2")
    url = reverse("cues:settings_picture", args=[project.pk])
    data = {"frame_rate": "24", "lanes": "2"}
    page = editor.post(url, data | {"picture_type": "reels"}).content.decode()
    assert "Reel names are numbers" in page
    page = editor.post(
        url, data | {"picture_type": "long_play"}
    ).content.decode()
    assert "Long play has one picture, and this project has 2." in page
    project.refresh_from_db()
    assert project.picture_type == "clips"
    assert editor.post(url, data | {"picture_type": "clips"})["Location"] == url


def test_a_long_play_turnover_asks_only_for_in_and_out(editor: Client) -> None:
    project = factories.ProjectFactory.create(
        picture_type="long_play", frame_rate="24"
    )
    url = reverse("cues:turnover_create", args=[project.pk])
    page = editor.get(url).content.decode()
    assert "<legend>The Picture</legend>" in page
    assert 'name="new-0-name"' not in page
    data = {
        "label": "LOCK",
        "received_date": "2026-10-30",
        "notes": "",
        "reels-TOTAL_FORMS": "0",
        "reels-INITIAL_FORMS": "0",
        "reels-MIN_NUM_FORMS": "0",
        "reels-MAX_NUM_FORMS": "200",
        "new-TOTAL_FORMS": "3",
        "new-INITIAL_FORMS": "0",
        "new-MIN_NUM_FORMS": "0",
        "new-MAX_NUM_FORMS": "40",
        "new-0-tc_in": "1:00:00:00",
        "new-0-tc_out": "2:45:00:00",
    }
    assert editor.post(url, data).status_code == 302
    assert list(project.pictures.values_list("name", flat=True)) == ["1"]
    # The next turnover updates that picture; there's no adding another.
    page = editor.get(url).content.decode()
    assert "<legend>Pictures in This Turnover</legend>" in page
    assert "The Picture</legend>" not in page


def test_a_type_the_pictures_fit_is_saved(editor: Client) -> None:
    project = factories.ProjectFactory.create(
        picture_type="clips", frame_rate="24"
    )
    _log(project, "1", "2")  # numbers are fine clip names, and reel names
    url = reverse("cues:settings_picture", args=[project.pk])
    data = {"frame_rate": "24", "lanes": "2", "picture_type": "reels"}
    assert editor.post(url, data)["Location"] == url
    project.refresh_from_db()
    assert project.picture_type == "reels"


@pytest.mark.parametrize(
    ("kind", "names", "choices"),
    [
        (
            "acts",
            ("1", "2"),
            ["The Act at Their IN", "No Act Yet", "Act 1", "Act 2"],
        ),
        (
            "clips",
            ("A12",),
            ["The Clip at Their IN", "No Clip Yet", "Clip A12"],
        ),
        ("long_play", ("1",), ["The Picture", "No Picture Yet", "Picture"]),
    ],
)
def test_import_places_new_cues_in_the_types_words(
    editor: Client, kind: str, names: tuple[str, ...], choices: list[str]
) -> None:
    project = factories.ProjectFactory.create(
        picture_type=kind, frame_rate="24"
    )
    project_service.placeholder_picture(project)
    _log(project, *names)
    form = transfer_forms.ImportForm(project=project)
    assert [c.choice_label for c in form["picture"]] == choices
    html = editor.get(reverse("cues:import", args=[project.pk])).content
    assert f">{choices[0]}</option>".encode() in html
