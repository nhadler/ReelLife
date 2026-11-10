"""A project's colour: chosen in settings, tinting its pages."""

from collections.abc import Callable
import re

from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


def test_a_projects_colour_tints_its_pages(editor: Client) -> None:
    project = factories.ProjectFactory.create(title="Tinted")
    url = reverse("cues:settings", args=[project.pk])
    page = editor.get(url).content.decode()
    automatic = project.pk % 8
    assert f'class="project-bar" data-tone="{automatic}"' in page
    assert re.search(r'name="color" value="" [^>]*checked', page)
    assert ">Automatic</span>" in page
    assert f"swatches__choice tone-{automatic} swatches__choice--auto" in page
    data = {"title": "Tinted", "kind": "film", "color": "violet"}
    assert editor.post(url, data)["Location"] == url
    project.refresh_from_db()
    assert project.color == "violet" and project.tone == 3
    grid = editor.get(reverse("cues:project", args=[project.pk])).content
    assert b'class="project-bar" data-tone="3"' in grid
    cards = editor.get(reverse("cues:dashboard")).content.decode()
    assert "project tone-3" in cards
    bad = editor.post(url, {**data, "color": "green"})
    assert bad.status_code == 200
    assert "green is not one of the available choices" in bad.content.decode()
