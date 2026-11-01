"""New Project's optional first turnover (Nick, Nov 1)."""

from collections.abc import Callable

from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from cues import models
from tests.medium.test_project_pages import new_project_data

pytestmark = pytest.mark.django_db


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


def test_create_project_with_its_first_turnover(editor: Client) -> None:
    data = new_project_data(
        **{
            "turnover-label": "LOCK",
            "turnover-received_date": "2026-10-30",
            "reels-0-name": "1",
            "reels-0-tc_in": "1:00:00:00",
            "reels-0-tc_out": "1:20:00:00",
            "reels-1-name": "2",
            "reels-1-tc_in": "2:00:00:00",
            "reels-1-tc_out": "2:18:30:00",
        }
    )
    response = editor.post(reverse("cues:project_create"), data, follow=True)
    project = models.Project.objects.get(title="The Big Heist")
    assert [str(m) for m in response.context["messages"]] == [
        "Created BH The Big Heist with turnover LOCK."
    ]
    turnover = project.turnovers.get()
    reels = {
        v.picture.name: (v.tc_in, v.tc_out)
        for v in turnover.picture_versions.select_related("picture")
    }
    assert reels == {"1": (86_400, 115_200), "2": (172_800, 199_440)}


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        (
            {"turnover-label": "LOCK"},
            "Add at least one reel with its IN and OUT.",
        ),
        (
            {
                "reels-0-name": "1",
                "reels-0-tc_in": "1:00:00:00",
                "reels-0-tc_out": "1:20:00:00",
            },
            "Name the turnover, e.g. LOCK.",
        ),
        (
            {
                "turnover-label": "LOCK",
                "reels-0-name": "1",
                "reels-0-tc_in": "1:00:00:00",
                "reels-0-tc_out": "1:20:00:00",
            },
            "Give the date it arrived.",
        ),
        (
            {
                "turnover-label": "LOCK",
                "turnover-received_date": "2026-10-30",
                "reels-0-name": "1",
                "reels-0-tc_in": "1:00:00:00",
            },
            "Give its IN and OUT.",
        ),
    ],
)
def test_an_incomplete_first_turnover_is_refused(
    editor: Client, fields: dict[str, str], error: str
) -> None:
    page = editor.post(
        reverse("cues:project_create"), new_project_data(**fields)
    ).content.decode()
    assert error in page
    assert '<details class="fold" open>' in page
    assert not models.Project.objects.filter(title="The Big Heist").exists()


def test_a_long_play_first_turnover_needs_no_name(editor: Client) -> None:
    data = new_project_data(
        **{
            "picture_type": "long_play",
            "turnover-label": "LOCK",
            "turnover-received_date": "2026-10-30",
            "reels-0-tc_in": "1:00:00:00",
            "reels-0-tc_out": "2:45:00:00",
        }
    )
    editor.post(reverse("cues:project_create"), data)
    project = models.Project.objects.get(title="The Big Heist")
    assert list(project.pictures.values_list("name", flat=True)) == ["1"]
