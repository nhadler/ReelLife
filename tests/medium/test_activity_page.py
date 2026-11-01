"""A project's Activity page: every change set, with Undo and Redo."""

from collections.abc import Callable
from html import unescape

from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from cues import models
from cues.services import history
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    return factories.ProjectFactory.create(title="The Voyage")


def _url(project: models.Project, page: int | None = None) -> str:
    url = reverse("cues:activity", args=[project.pk])
    return f"{url}?page={page}" if page else url


def _undo_url(change_set: models.ChangeSet) -> str:
    assert change_set.project is not None
    return reverse(
        "cues:activity_undo", args=[change_set.project.pk, change_set.pk]
    )


def _page(client: Client, project: models.Project, page: int = 0) -> str:
    return unescape(client.get(_url(project, page)).content.decode())


def _retitle(cue: models.Cue, title: str) -> models.ChangeSet:
    with history.recording(cue.project, f"Edit {cue.number} Title") as cs:
        cue.title = title
        cue.save()
    return cs


def test_the_page_lists_change_sets_newest_first(
    editor: Client, project: models.Project
) -> None:
    cue = factories.CueFactory.create(project=project, number="1m01", title="")
    _retitle(cue, "One")
    with history.via("MCP, claude.ai"):
        _retitle(cue, "Two")
    page = _page(editor, project)
    newer = page.index("Edit 1m01 Title (via MCP, claude.ai)")
    assert newer < page.index("Edit 1m01 Title<")
    assert page.count('class="activity__cues">1<') == 2
    assert "Changed Since" in page  # the older one waits for the newer
    assert page.count("Undo<span") == 1


def test_viewers_see_it_without_buttons(
    role_client: Callable[[Role], Client], project: models.Project
) -> None:
    _retitle(factories.CueFactory.create(project=project), "One")
    page = _page(role_client(Role.VIEWER), project)
    assert "Edit " in page
    assert "Undo<span" not in page


def test_any_change_set_can_be_undone_when_nothing_changed_it_since(
    editor: Client, project: models.Project
) -> None:
    first = factories.CueFactory.create(
        project=project, number="1m01", title=""
    )
    second = factories.CueFactory.create(
        project=project, number="1m02", title=""
    )
    older = _retitle(first, "One")
    _retitle(second, "Two")
    response = editor.post(_undo_url(older), {"page": "1"})
    assert response["Location"] == _url(project, 1)
    first.refresh_from_db()
    assert first.title == ""
    assert "Undid Edit 1m01 Title." in _page(editor, project)


def test_undo_refuses_when_changed_afterwards(
    editor: Client, project: models.Project
) -> None:
    cue = factories.CueFactory.create(project=project, number="1m01", title="")
    older = _retitle(cue, "One")
    _retitle(cue, "Two")
    response = editor.post(_undo_url(older), {"page": "x"})
    assert response["Location"] == _url(project)
    assert "was changed afterwards; undo that first." in _page(editor, project)
    cue.refresh_from_db()
    assert cue.title == "Two"


def test_an_undo_can_be_redone_and_undone_again(
    editor: Client, project: models.Project
) -> None:
    cue = factories.CueFactory.create(project=project, number="1m01", title="")
    change_set = _retitle(cue, "One")
    editor.post(_undo_url(change_set))
    page = _page(editor, project)
    assert "Undone" in page and "Redo<span" in page
    undo = models.ChangeSet.objects.get(undo_of=change_set)
    editor.post(_undo_url(undo))
    cue.refresh_from_db()
    assert cue.title == "One"
    assert "Redid Edit 1m01 Title." in _page(editor, project)
    redo = models.ChangeSet.objects.get(undo_of=undo)
    editor.post(_undo_url(redo))
    cue.refresh_from_db()
    assert cue.title == ""
    assert "Undid Edit 1m01 Title." in _page(editor, project)


def test_another_projects_change_set_is_not_found(
    editor: Client, project: models.Project
) -> None:
    other = _retitle(factories.CueFactory.create(), "Elsewhere")
    url = reverse("cues:activity_undo", args=[project.pk, other.pk])
    assert editor.post(url).status_code == 404


def test_the_page_is_paginated(editor: Client, project: models.Project) -> None:
    for number in range(51):
        with history.recording(project, f"Change {number}"):
            pass
    first = _page(editor, project)
    assert "Change 50" in first and "Change 0<" not in first
    assert "Page 1 of 2" in first and "Older" in first
    second = _page(editor, project, 2)
    assert "Change 0<" in second and "Newer" in second


def test_an_empty_project_says_so(
    editor: Client, project: models.Project
) -> None:
    assert "No Changes Yet" in _page(editor, project)
