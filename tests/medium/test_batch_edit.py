"""Batch edit: one value for several selected cues."""

from collections.abc import Callable
import json
from typing import Any

from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from cues import models
from cues.api import edits
from cues.services import history
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


@pytest.fixture(name="cues")
def fixture_cues() -> list[models.Cue]:
    project = factories.ProjectFactory.create()
    picture = factories.PictureFactory.create(project=project)
    return [
        factories.CueFactory.create(
            project=project, picture=picture, number=f"1m0{i}"
        )
        for i in range(1, 4)
    ]


def _post(client: Client, project: models.Project, body: Any) -> Any:
    return client.post(
        reverse("cues:api_cues_edit", args=[project.pk]),
        json.dumps(body),
        content_type="application/json",
    )


def test_one_change_set_and_undo(
    editor: Client, cues: list[models.Cue]
) -> None:
    project = cues[0].project
    composed = project.statuses.get(kind=models.StatusKind.DONE)
    ids = [cue.pk for cue in cues[:2]]
    response = _post(
        editor, project, {"ids": ids, "field": "status", "value": composed.pk}
    )
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert sorted(row["id"] for row in rows) == sorted(ids)
    assert {c.status_id for c in models.Cue.objects.filter(pk__in=ids)} == {
        composed.pk
    }
    change = models.ChangeSet.objects.filter(project=project).latest("pk")
    assert change.label == "Edit 2 cues Status"
    history.undo(change)
    assert composed.pk not in {
        c.status_id for c in models.Cue.objects.filter(pk__in=ids)
    }


def test_version_fields_and_new_sub_statuses(
    editor: Client, cues: list[models.Cue]
) -> None:
    project = cues[0].project
    ids = [cue.pk for cue in cues]
    response = _post(
        editor, project, {"ids": ids, "field": "sub_status", "value": "Hold"}
    )
    assert response.status_code == 200
    option = project.sub_statuses.get(label="Hold")
    for cue in models.Cue.objects.filter(pk__in=ids):
        assert cue.current_version is not None
        assert cue.current_version.sub_status_id == option.pk


def test_refusals_change_nothing(
    editor: Client, cues: list[models.Cue]
) -> None:
    project = cues[0].project
    ids = [cue.pk for cue in cues]
    before = models.ChangeSet.objects.count()
    for body, message in [
        ({"ids": ids, "field": "tc_in", "value": "1:00:00:00"}, "IN can't"),
        ({"ids": ids, "field": "priority", "value": "nope"}, "1m01:"),
        ({"ids": ids, "field": "nope", "value": 1}, "can't be edited"),
        ({"ids": [], "field": "priority", "value": ""}, "between one"),
        ({"ids": ["x"], "field": "priority"}, "Send JSON"),
        ({"field": "priority"}, "Send JSON"),
    ]:
        response = _post(editor, project, body)
        assert response.status_code == 400
        assert message in response.json()["error"]
    other = factories.CueFactory.create()
    response = _post(
        editor,
        project,
        {"ids": [*ids, other.pk], "field": "priority", "value": ""},
    )
    assert "aren't in this project" in response.json()["error"]
    assert models.ChangeSet.objects.count() == before


def test_a_cue_refusing_names_it(cues: list[models.Cue]) -> None:
    project = cues[0].project
    blocked = project.statuses.first()
    assert blocked is not None
    with pytest.raises(edits.EditError, match="^1m01: "):
        edits.apply_many(cues, "status", 999_999)


def test_viewers_cannot(
    role_client: Callable[[Role], Client], cues: list[models.Cue]
) -> None:
    viewer = role_client(Role.VIEWER)
    response = _post(
        viewer,
        cues[0].project,
        {"ids": [cues[0].pk], "field": "priority", "value": ""},
    )
    assert response.status_code == 403


def test_the_overview_knows_the_endpoint(
    editor: Client, cues: list[models.Cue]
) -> None:
    project = cues[0].project
    html = editor.get(reverse("cues:project", args=[project.pk])).content
    assert reverse("cues:api_cues_edit", args=[project.pk]).encode() in html
    assert b"notBatch" in html


def test_a_value_the_model_refuses_names_the_cue(
    cues: list[models.Cue],
) -> None:
    with pytest.raises(edits.EditError, match="^1m01: .*at most 200"):
        edits.apply_many(cues, "title", "x" * 201)
