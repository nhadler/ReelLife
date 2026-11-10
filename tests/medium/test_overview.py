"""The Overview, the home page: the next seven days and active projects."""

from collections.abc import Callable, Iterator
import datetime

from django.test import Client
import freezegun
import pytest

from accounts.models import Role
from cues import models
from tests import factories

pytestmark = pytest.mark.django_db

MON = datetime.date(2026, 11, 2)


@pytest.fixture(autouse=True)
def fixture_today() -> Iterator[None]:
    with freezegun.freeze_time("2026-11-02 18:00:00"):
        yield


def _project(title: str, due: datetime.date | None) -> models.Project:
    project = factories.ProjectFactory.create(title=title, frame_rate="24")
    if due:
        factories.DeadlineFactory.create(project=project, end_date=due)
    picture = factories.PictureFactory.create(project=project, name="1")
    factories.CueFactory.create(
        project=project,
        picture=picture,
        number="1m01",
        title="Main Title",
        status=project.statuses.get(kind=models.StatusKind.TO_DO),
        tc_in=86_400,
        tc_out=86_400 + 24 * 60,
    )
    return project


def test_the_home_page_is_the_overview(
    role_client: Callable[[Role], Client],
) -> None:
    _project("Night Ferry", MON + datetime.timedelta(days=20))
    _project("Harbour Lights", MON + datetime.timedelta(days=1))
    _project("No Deadline", None)
    factories.ProjectFactory.create(title="Old Film", archived=True)
    editor = role_client(Role.EDITOR)
    page = editor.get("/").content.decode()
    assert 'aria-current="page">Overview</a>' in page
    assert "New Project" in page
    # Seven days from today, each opening the Schedule on that day.
    assert page.count('<li class="cal-day') == 7
    assert "/schedule/?view=week&amp;week=2026-11-02&amp;day=2026-11-02" in page
    assert "Main Title" in page
    # Active projects, soonest deadline first, none last; not archived.
    order = [
        page.index(t) for t in ("Harbour Lights", "Night Ferry", "No Deadline")
    ]
    assert order == sorted(order)
    assert "Old Film" not in page
    # Harbour Lights's minute, in its This Week figure.
    assert '<span class="project__big">1:00</span>' in page
    viewer = role_client(Role.VIEWER).get("/").content.decode()
    assert "New Project" not in viewer


def test_no_active_projects(role_client: Callable[[Role], Client]) -> None:
    page = role_client(Role.EDITOR).get("/").content.decode()
    assert "No Active Projects" in page
    assert "Nothing left to write." in page


def test_projects_moved_to_their_own_page(
    role_client: Callable[[Role], Client],
) -> None:
    _project("Night Ferry", None)
    page = role_client(Role.EDITOR).get("/projects/").content.decode()
    assert 'aria-current="page">Projects</a>' in page
    assert 'class="project tone-' in page
