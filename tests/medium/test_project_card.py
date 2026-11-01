"""The project card on Projects and the Overview (design review, Nov 1)."""

from collections.abc import Callable
import datetime

from django.test import Client
from django.urls import reverse
from django.utils import timezone
import pytest

from accounts.models import Role
from cues import models
from cues.services import demo
from cues.templatetags import text
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("title", "initials"),
    [
        ("The Lighthouse Keeper", "LK"),
        ("Harbour Lights", "HL"),
        ("test", "T"),
        ("The", "T"),
    ],
)
def test_initials(title: str, initials: str) -> None:
    assert text.initials(title) == initials


def test_the_card_shows_figures_not_sentences(
    role_client: Callable[[Role], Client],
) -> None:
    project = demo.build_demo().project
    project.deadlines.all().delete()
    models.Deadline.objects.create(
        project=project,
        kind=models.Deadline.Kind.COMPOSITION,
        end_date=timezone.localdate() + datetime.timedelta(days=5),
    )
    page = role_client(Role.EDITOR).get(reverse("cues:dashboard"))
    html = page.content.decode()
    assert '<span class="project__big">61%</span>' in html
    assert "23 of 36 cues" in html
    assert "project__figure--due" in html  # 5 days: in red
    assert "on older picture</a>" in html  # amber, links to Conform
    assert 'class="cuemap__todo"' in html  # unwritten cues stay quiet
    assert "Across your projects" not in html
    summary = html[html.index("page-head__summary") :]
    summary = " ".join(summary[: summary.index("</p>")].split())
    # One project: no across-projects pace (that needs two; test_pace).
    assert summary.endswith('page-head__summary">1 active'), summary
    assert "data-bulk hidden" in html  # the bar waits for a tick
    assert 'class="project__check"' in html


def test_a_viewer_gets_no_checkbox_or_actions(
    role_client: Callable[[Role], Client],
) -> None:
    factories.ProjectFactory.create(title="Night Ferry")
    html = role_client(Role.VIEWER).get(reverse("cues:dashboard"))
    page = html.content.decode()
    assert "Night Ferry" in page
    assert "project__check" not in page
    assert 'value="archive"' not in page
    assert "a day across them" not in page


def test_the_more_menu_archives_one_project(
    role_client: Callable[[Role], Client],
) -> None:
    project = factories.ProjectFactory.create(title="Old Film")
    other = factories.ProjectFactory.create(title="Keep Me")
    editor = role_client(Role.EDITOR)
    page = editor.get(reverse("cues:dashboard")).content.decode()
    assert f'<input type="hidden" name="project" value="{project.pk}">' in page
    response = editor.post(
        reverse("cues:projects_bulk"),
        {"project": str(project.pk), "action": "archive", "next": "/"},
    )
    assert response.status_code == 302
    project.refresh_from_db()
    other.refresh_from_db()
    assert project.archived and not other.archived
    archived = editor.get(reverse("cues:dashboard"), {"archived": "on"})
    assert 'value="unarchive"' in archived.content.decode()
