"""Views: add, change, copy on first change, reset and delete."""

from collections.abc import Callable
import json
from typing import Any

from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from cues import models
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    return factories.ProjectFactory.create()


def _standard() -> models.View:
    return models.View.objects.get(project=None, name="Standard")


def _create(client: Client, project: models.Project, body: Any) -> Any:
    return client.post(
        reverse("cues:api_views", args=[project.pk]),
        json.dumps(body),
        content_type="application/json",
    )


def _change(
    client: Client,
    project: models.Project,
    view: models.View,
    body: Any = None,
    *,
    method: str = "patch",
) -> Any:
    url = reverse("cues:api_view", args=[project.pk, view.pk])
    if method == "delete":
        return client.delete(url)
    return client.patch(url, json.dumps(body), content_type="application/json")


def _names(response: Any) -> list[str]:
    return [view["name"] for view in response.json()["views"]]


def test_adding_and_duplicating_a_view(
    editor: Client, project: models.Project
) -> None:
    columns = [
        {"id": "number"},
        {"id": "title", "width": 240, "pinned": "left"},
    ]
    response = _create(
        editor,
        project,
        {
            "name": " Writing ",
            "columns": columns,
            "sort": [{"id": "number", "dir": "desc"}],
        },
    )
    assert response.status_code == 201
    view = response.json()["view"]
    assert (view["name"], view["builtIn"], view["changed"]) == (
        "Writing",
        False,
        False,
    )
    assert view["columns"] == columns
    copy = _create(editor, project, {"name": "Writing 2", "columns": columns})
    grid = editor.get(reverse("cues:api_grid", args=[project.pk])).json()
    names = [v["name"] for v in grid["views"]]
    assert names[-2:] == ["Writing", "Writing 2"]  # after the seeded eight
    assert copy.json()["view"]["sort"] == []


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ({"name": "", "columns": [{"id": "title"}]}, "needs a name"),
        ({"name": "x" * 51, "columns": [{"id": "title"}]}, "needs a name"),
        ({"name": "standard", "columns": [{"id": "title"}]}, "already a view"),
        ({"name": "A", "columns": []}, "at least one column"),
        ({"name": "A", "columns": [{"id": "nope"}]}, "aren't ones"),
        ({"name": "A", "columns": ["title"]}, "aren't ones"),
        (
            {"name": "A", "columns": [{"id": "title"}, {"id": "title"}]},
            "aren't ones",
        ),
        ({"name": "A", "columns": [{"id": "title", "width": 5}]}, "30 to 2000"),
        (
            {"name": "A", "columns": [{"id": "title", "width": True}]},
            "30 to 2000",
        ),
        ({"name": "A", "columns": [{"id": "title", "pinned": "up"}]}, "pinned"),
        (
            {"name": "A", "columns": [{"id": "title"}], "sort": [{"id": "x"}]},
            "sort isn't",
        ),
        ([1, 2], "JSON object"),
    ],
)
def test_bad_views_are_refused(
    editor: Client, project: models.Project, body: Any, error: str
) -> None:
    response = _create(editor, project, body)
    assert response.status_code == 400
    assert error in response.json()["error"]
    assert not project.views.exists()


def test_changing_a_seeded_view_copies_it_into_the_project(
    editor: Client, project: models.Project
) -> None:
    other = factories.ProjectFactory.create()
    standard = _standard()
    before = standard.columns
    response = _change(
        editor, project, standard, {"columns": [{"id": "title", "width": 300}]}
    )
    assert response.status_code == 200
    view = response.json()["view"]
    assert (view["name"], view["builtIn"], view["changed"]) == (
        "Standard",
        True,
        True,
    )
    copy = project.views.get()
    assert copy.pk == view["id"] != standard.pk
    standard.refresh_from_db()
    assert standard.columns == before  # other projects keep theirs
    other_grid = editor.get(reverse("cues:api_grid", args=[other.pk])).json()
    assert other_grid["views"][0]["id"] == standard.pk
    # The copy is changed in place from now on, in the seeded order.
    response = _change(
        editor, project, copy, {"sort": [{"id": "title", "dir": "asc"}]}
    )
    assert project.views.count() == 1
    assert _names(response)[0] == "Standard"
    # Deleting the copy resets the view.
    response = _change(editor, project, copy, method="delete")
    assert response.json()["view"] is None
    assert not project.views.exists()
    assert response.json()["views"][0]["id"] == standard.pk


def test_renaming(editor: Client, project: models.Project) -> None:
    response = _change(editor, project, _standard(), {"name": "Mine"})
    assert response.status_code == 400
    assert "keeps its name" in response.json()["error"]
    own = _create(
        editor, project, {"name": "Draft", "columns": [{"id": "title"}]}
    ).json()["view"]
    view = models.View.objects.get(pk=own["id"])
    response = _change(
        editor, project, view, {"name": "Final", "columns": [{"id": "number"}]}
    )
    assert response.json()["view"]["name"] == "Final"
    # Unchanged names are fine for seeded views too.
    response = _change(editor, project, _standard(), {"name": "Standard"})
    assert response.status_code == 200


def test_deleting(editor: Client, project: models.Project) -> None:
    response = _change(editor, project, _standard(), method="delete")
    assert response.status_code == 400
    assert "can't be deleted" in response.json()["error"]
    own = factories.ViewFactory.create(project=project, name="Draft")
    response = _change(editor, project, own, method="delete")
    assert "Draft" not in _names(response)


def test_another_projects_view_is_refused(
    editor: Client, project: models.Project
) -> None:
    theirs = factories.ViewFactory.create(name="Theirs")  # another project
    for method, body in (("patch", {"name": "Mine"}), ("delete", None)):
        response = _change(editor, project, theirs, body, method=method)
        assert response.status_code == 400
        assert "another project" in response.json()["error"]


def test_viewers_cannot_change_views(
    role_client: Callable[[Role], Client], project: models.Project
) -> None:
    viewer = role_client(Role.VIEWER)
    body = {"name": "A", "columns": [{"id": "title"}]}
    assert _create(viewer, project, body).status_code == 403
    assert _change(viewer, project, _standard(), body).status_code == 403


def test_a_body_that_isnt_json_is_refused(
    editor: Client, project: models.Project
) -> None:
    response = editor.post(
        reverse("cues:api_views", args=[project.pk]),
        "not json",
        content_type="application/json",
    )
    assert response.status_code == 400
    assert "JSON object" in response.json()["error"]
