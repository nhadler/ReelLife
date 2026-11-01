"""Empty pages say so in one line, with the action that fills them."""

from collections.abc import Callable

from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role

pytestmark = pytest.mark.django_db


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


def test_empty_dashboard_invites_a_first_project(
    editor: Client, role_client: Callable[[Role], Client]
) -> None:
    html = editor.get(reverse("cues:dashboard")).content.decode()
    assert "No Projects Yet" in html
    create = reverse("cues:project_create")
    assert (
        f'<a class="button button--quiet" href="{create}">New Project</a>'
        in html
    )
    searched = editor.get(reverse("cues:dashboard"), {"q": "ferry"})
    assert "Clear Search" in searched.content.decode()
    viewer = role_client(Role.VIEWER)
    seen = viewer.get(reverse("cues:dashboard")).content.decode()
    assert "No Projects Yet" in seen
    assert f'href="{create}"' not in seen
