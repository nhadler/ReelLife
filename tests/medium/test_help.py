"""The user guide: Help pages, and the MCP server's how-to."""

from collections.abc import Callable, Iterator
from typing import Any

from django.test import Client
from django.urls import reverse
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
import pytest

from accounts.models import Role
from accounts.models import User
from cues.services import guide
from mcp_server import server
from reellife import updates
from tests import factories

pytestmark = pytest.mark.django_db


def test_every_guide_file_is_a_listed_topic() -> None:
    files = {path.stem for path in guide.GUIDE.glob("*.md")}
    assert files == set(guide.ORDER)
    for topic in guide.topics():
        assert topic.text.startswith(f"# {topic.title}\n")


def test_help_pages(role_client: Callable[[Role], Client]) -> None:
    viewer = role_client(Role.VIEWER)
    first = viewer.get(reverse("cues:help")).content.decode()
    assert f"ReelLife {updates.installed()}" in first
    assert 'href="https://github.com/nhadler/reellife">Source Code</a>' in first
    assert '<h1 class="page-title help__title">Getting Started</h1>' in first
    assert "<li>On <strong>Projects</strong>, choose" in first
    page = viewer.get(reverse("cues:help_topic", args=["conform"]))
    assert 'aria-current="page">Conform</a>' in page.content.decode()
    missing = viewer.get(reverse("cues:help_topic", args=["nothing"]))
    assert missing.status_code == 404
    home = viewer.get("/").content.decode()
    assert 'href="/help/" data-palette="Help">Help</a>' in home


def test_the_assistant_reads_the_guide(acting_as: Any) -> None:
    del acting_as
    listing = server.user_guide()
    assert {"topic": "importing", "title": "Importing"} in listing["topics"]
    page = server.user_guide("Importing ")
    assert page["title"] == "Importing" and "Pro Tools" in page["guide"]
    assert "No topic" in server.user_guide("bananas")["note"]
    assert (
        server.user_guide("all")["guide"].count("\n# ") == len(guide.ORDER) - 1
    )
    assert server.guide_resource().startswith("# Getting Started")


@pytest.fixture(name="acting_as")
def fixture_acting_as() -> Iterator[User]:
    """A viewer, as the MCP SDK's middleware would set them."""
    user = factories.UserFactory.create(role=Role.VIEWER)
    token = auth_context_var.set(
        AuthenticatedUser(
            AccessToken(
                token="t", client_id="c", scopes=[], claims={"user_id": user.pk}
            )
        )
    )
    yield user
    auth_context_var.reset(token)


@pytest.mark.parametrize(
    ("page", "topic"),
    [
        ("cues:schedule", "schedule"),
        ("cues:credits", "credits-and-cue-sheets"),
        ("cues:app_settings", "settings-and-roles"),
        ("cues:days_off", "progress-and-deadlines"),
        ("cues:project_create", "getting-started"),
    ],
)
def test_pages_link_their_help_topic(
    role_client: Callable[[Role], Client], page: str, topic: str
) -> None:
    html = role_client(Role.EDITOR).get(reverse(page)).content.decode()
    href = reverse("cues:help_topic", args=[topic])
    assert f'class="help-link" href="{href}"' in html
