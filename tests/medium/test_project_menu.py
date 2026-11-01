"""The project bar's Settings menu (Nick, Nov 1)."""

from collections.abc import Callable

from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from tests import factories

pytestmark = pytest.mark.django_db


def test_the_more_menu_lists_everything_else(
    role_client: Callable[[Role], Client],
) -> None:
    project = factories.ProjectFactory.create()
    editor = role_client(Role.EDITOR)
    page = editor.get(
        reverse("cues:project", args=[project.pk])
    ).content.decode()
    menu = page[page.index('class="menu project-menu"') :]
    menu = menu[: menu.index("</details>")]
    assert "<span>More</span>" in menu
    # Picture and Notes are in the bar itself, so not repeated here.
    assert "Picture &amp; Turnovers" not in menu
    assert "Project Notes" not in menu
    for item in (
        "Conform",
        'data-grid-action="stats"',
        reverse("cues:activity", args=[project.pk]),
        'data-palette="Activity"',
        reverse("cues:trash", args=[project.pk]),
        'data-palette="Trash"',
        "Import Cues",
        'data-grid-action="export"',
        "Export Cue Sheet",
        'data-grid-action="copy-json"',
        reverse("cues:settings_statuses", args=[project.pk]),
        "Project (Archive, Duplicate…)",
    ):
        assert item in menu, item
    # Off the cue overview, grid actions open it to do them.
    picture = editor.get(
        reverse("cues:picture", args=[project.pk])
    ).content.decode()
    assert f"{reverse('cues:project', args=[project.pk])}?do=stats" in picture
    viewer = role_client(Role.VIEWER)
    seen = viewer.get(
        reverse("cues:project", args=[project.pk])
    ).content.decode()
    assert "Import Cues<kbd>" not in seen[seen.index("project-menu") :]


@pytest.mark.parametrize(
    ("page", "current"),
    [
        ("cues:conform", "menu"),
        ("cues:activity", "menu"),
        ("cues:settings", "menu"),
        ("cues:picture", "picture"),
        ("cues:project", "cues"),
    ],
)
def test_the_bar_marks_the_page_youre_on(
    role_client: Callable[[Role], Client], page: str, current: str
) -> None:
    project = factories.ProjectFactory.create()
    html = role_client(Role.EDITOR).get(reverse(page, args=[project.pk]))
    text = html.content.decode()
    picture = reverse("cues:picture", args=[project.pk])
    cues = reverse("cues:project", args=[project.pk])
    assert ("project-menu__button--current" in text) == (current == "menu")
    assert (f'href="{picture}" aria-current="page"' in text) == (
        current == "picture"
    )
    assert (f'href="{cues}" aria-current="page"' in text) == (current == "cues")
    assert ">Cues</span></a>" in text  # always "Cues", never swapped
