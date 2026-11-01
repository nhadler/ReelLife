"""MCP tools that change things: edits, cues, versions, setup."""

import asyncio
from collections.abc import Iterator
import datetime
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
import pytest

from accounts.models import Role
from accounts.models import User
from cues import models
from cues.services import authoring
from cues.services import demo
from cues.services import history
from mcp_server import common
from mcp_server import setup
from mcp_server import writes
from tests import factories

pytestmark = pytest.mark.django_db


def _token(user: User | None, client_id: str = "https://claude.ai/x") -> Any:
    return AuthenticatedUser(
        AccessToken(
            token="t",
            client_id=client_id,
            scopes=[],
            claims={"user_id": user.pk if user else None},
        )
    )


@pytest.fixture(name="editor")
def fixture_editor() -> Iterator[User]:
    """Tools act for an editor, as the SDK's middleware would set."""
    user = factories.UserFactory.create(role=Role.EDITOR)
    token = auth_context_var.set(_token(user))
    yield user
    auth_context_var.reset(token)


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    return demo.build_demo().project


def _latest(project: models.Project) -> models.ChangeSet:
    return models.ChangeSet.objects.filter(project=project).latest("pk")


def test_viewers_cannot_change_anything(project: models.Project) -> None:
    viewer = factories.UserFactory.create(role=Role.VIEWER)
    token = auth_context_var.set(_token(viewer))
    try:
        cue = project.cues.first()
        assert cue is not None
        with pytest.raises(ToolError, match="editor role"):
            writes.edit_cue(cue.pk, "title", "Nope")
        assert writes.cue_fields(project.pk)  # reading is fine
    finally:
        auth_context_var.reset(token)


def test_cue_fields(editor: User, project: models.Project) -> None:
    del editor
    fields = {f["field"]: f for f in writes.cue_fields(project.pk)}
    assert "Approved" in fields["status"]["options"]
    assert fields["tc_in"]["value"].startswith("timecode")
    assert fields["picture"]["options"] == ["1", "2", "3"]
    assert "BI" in fields["usage"]["options"]


def test_edit_cue_takes_values_as_people_write_them(
    editor: User, project: models.Project
) -> None:
    del editor
    cue = project.cues.get(number="1m01")
    assert writes.edit_cue(cue.pk, "status", "approved")["cue"]["status"] == (
        "Approved"
    )
    assert writes.edit_cue(cue.pk, "picture", "2")["cue"]["reel"] == "2"
    writes.edit_cue(cue.pk, "usage", "Visual Instrumental")
    writes.edit_cue(cue.pk, "instrumentation", ["Cello", "Harp 1:15"])
    writes.edit_cue(cue.pk, "lane", "1")
    cue.refresh_from_db()
    assert cue.usage == "VI"
    assert cue.lane == 1
    assert [
        (i.instrument.name, i.seconds) for i in cue.instrumentation.all()
    ] == [
        ("Cello", None),
        ("Harp", 75),
    ]
    writes.edit_cue(cue.pk, "themes", [{"name": "Sea", "seconds": 30}])
    with pytest.raises(ToolError, match="isn't a valid choice"):
        writes.edit_cue(cue.pk, "usage", "Loud")
    with pytest.raises(ToolError, match="No status 'Nope'"):
        writes.edit_cue(cue.pk, "status", "Nope")
    with pytest.raises(ToolError, match="No reel"):
        writes.edit_cue(cue.pk, "picture", "9")
    with pytest.raises(ToolError, match="can't be edited"):
        writes.edit_cue(cue.pk, "version", "v9")
    with pytest.raises(ToolError, match="No cue 0"):
        writes.edit_cue(0, "title", "x")


@pytest.mark.django_db(transaction=True)
def test_changes_are_marked_as_mcp_in_history(
    editor: User, project: models.Project
) -> None:
    del editor
    cue = project.cues.get(number="1m01")
    asyncio.run(common.off_loop(writes.edit_cue)(cue.pk, "title", "Changed"))
    assert _latest(project).label == "Edit 1m01 Title (via MCP, claude.ai)"


@pytest.mark.parametrize(
    ("client_id", "label"),
    [
        ("https://claude.ai/oauth/client", "MCP, claude.ai"),
        ("my-script", "MCP, my-script"),
        ("", "MCP"),
    ],
)
def test_client_label(client_id: str, label: str) -> None:
    token = auth_context_var.set(_token(None, client_id))
    try:
        assert common.client_label() == label
    finally:
        auth_context_var.reset(token)


def test_new_cue(editor: User, project: models.Project) -> None:
    del editor
    made = writes.new_cue(
        project.pk,
        "1m50",
        title="Tide",
        reel="1",
        tc_in="1:05:00:00",
        tc_out="1:05:30:00",
        status="In Progress",
        spotting_notes="Low strings",
    )["created"]
    assert (made["status"], made["reel"]) == ("In Progress", "1")
    cue = project.cues.get(number="1m50")
    assert cue.current_version is not None
    assert cue.current_version.spotting_notes == "Low strings"
    # The reel follows from IN when not named.
    reel_2 = project.pictures.get(name="2").current_version()
    assert reel_2 is not None and reel_2.tc_in is not None
    later = writes.new_cue(
        project.pk, "2m50", tc_in=common.tc(reel_2.tc_in + 48, project.rate)
    )
    assert later["created"]["reel"] == "2"
    with pytest.raises(ToolError, match="Cue #"):
        writes.new_cue(project.pk, "", reel="1")
    with pytest.raises(ToolError, match="Say which reel"):
        writes.new_cue(project.pk, "9m01")
    with pytest.raises(ToolError, match="IN:"):
        writes.new_cue(project.pk, "9m01", tc_in="soon")
    with pytest.raises(ToolError, match="No reel"):
        writes.new_cue(project.pk, "9m01", reel="9")
    with pytest.raises(ToolError, match="No status"):
        writes.new_cue(project.pk, "9m01", reel="1", status="Nope")
    with pytest.raises(ToolError, match="already has this number"):
        writes.new_cue(project.pk, "1m50", reel="1")


def test_new_cue_in_a_project_with_one_reel(editor: User) -> None:
    del editor
    project = factories.ProjectFactory.create()
    factories.PictureFactory.create(project=project, name="1")
    made = writes.new_cue(project.pk, "1m01")["created"]
    assert made["reel"] == "1"


def test_new_cue_that_fails_on_save(
    editor: User, project: models.Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    del editor

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise ValidationError("Nope.")

    monkeypatch.setattr(authoring, "create_cue", refuse)
    with pytest.raises(ToolError, match="Nope."):
        writes.new_cue(project.pk, "1m77", reel="1")


def test_add_version_and_undo(editor: User, project: models.Project) -> None:
    del editor
    cue = project.cues.get(number="1m01")
    before = cue.current_version
    added = writes.add_version(
        cue.pk, revision_notes="Shorter", status="Revisions"
    )
    assert added["cue"]["status"] == "Revisions"
    cue.refresh_from_db()
    assert cue.current_version is not None
    assert cue.current_version.revision_notes == "Shorter"
    assert writes.undo_last_change(project.pk)["undone"].startswith(
        "New version 1m01"
    )
    cue.refresh_from_db()
    assert cue.current_version == before
    with pytest.raises(ToolError, match="No status"):
        writes.add_version(cue.pk, status="Nope")
    with pytest.raises(ToolError, match="IN"):
        writes.add_version(cue.pk, tc_in="nonsense")
    bare = factories.CueFactory.create(
        project=project, picture=project.pictures.get(name="1"), number="9m99"
    )
    assert writes.add_version(bare.pk)["added"] == "v1"


def test_add_version_that_fails_on_save(
    editor: User, project: models.Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    del editor

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise ValidationError("Nope.")

    monkeypatch.setattr(authoring, "create_version", refuse)
    with pytest.raises(ToolError, match="Nope."):
        writes.add_version(project.cues.get(number="1m01").pk)


def test_undo_with_nothing_or_blocked(
    editor: User, project: models.Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    del editor
    with pytest.raises(ToolError, match="Nothing to undo"):
        writes.undo_last_change(project.pk)
    writes.edit_cue(project.cues.get(number="1m01").pk, "title", "X")

    def blocked(*args: Any) -> None:
        raise history.UndoError("Changed since.")

    monkeypatch.setattr(history, "undo", blocked)
    with pytest.raises(ToolError, match="Changed since."):
        writes.undo_last_change(project.pk)


# Setting a project up


def test_create_project_turnover_deadline_and_notes(editor: User) -> None:
    del editor
    made = setup.create_project("Harbour", production_number="HB1")
    project = models.Project.objects.get(pk=made["id"])
    assert project.statuses.exists()
    with pytest.raises(ToolError):
        setup.create_project("", frame_rate="23.976")
    logged = setup.log_turnover(
        project.pk,
        "c0917",
        "2026-09-17",
        [{"name": "1", "in": "01:00:00:00", "out": "01:20:00:00"}],
    )
    assert logged["label"] == "c0917"
    picture = project.pictures.get(name="1").current_version()
    assert picture is not None and picture.tc_out == 3600 * 24 + 1200 * 24
    with pytest.raises(ToolError, match="needs a name"):
        setup.log_turnover(project.pk, "c1", "2026-09-18", [{"in": "1"}])
    with pytest.raises(ToolError, match="Reel 1"):
        setup.log_turnover(
            project.pk, "c1", "2026-09-18", [{"name": "1", "in": "x"}]
        )
    with pytest.raises(ToolError, match="YYYY-MM-DD"):
        setup.log_turnover(project.pk, "c1", "soon", [{"name": "1"}])
    with pytest.raises(ToolError):
        setup.log_turnover(project.pk, "c0917", "2026-09-18", [{"name": "1"}])

    setup.set_deadline(project.pk, "composition", "2026-11-03", "2026-10-01")
    setup.set_deadline(project.pk, "composition", "2026-11-10")
    assert [str(d.end_date) for d in project.deadlines.all()] == ["2026-11-10"]
    setup.set_deadline(project.pk, "custom", "2026-12-01", label="Temp mix")
    assert project.deadlines.count() == 2
    with pytest.raises(ToolError, match="kind is one of"):
        setup.set_deadline(project.pk, "lunch", "2026-12-01")
    with pytest.raises(ToolError):
        setup.set_deadline(project.pk, "mix", "2026-12-01", "2026-12-09")

    setup.set_project_note(project.pk, "Strings only.")
    setup.set_project_note(project.pk, "And harp.")
    note = project.notes.get(kind="general")
    assert note.text == "Strings only.\n\nAnd harp."
    setup.set_project_note(project.pk, "Fresh.", append=False)
    note.refresh_from_db()
    assert note.text == "Fresh."
    with pytest.raises(ToolError, match="kind is one of"):
        setup.set_project_note(project.pk, "x", kind="lunch")


def test_credits_and_default_shares(editor: User) -> None:
    del editor
    project = factories.ProjectFactory.create()
    writer = setup.add_credit(
        "writer", first_name="Ann", last_name="Lee", society="bmi"
    )
    assert writer["new"] and writer["name"] == "Ann Lee (BMI)"
    again = setup.add_credit("writer", first_name="Ann", last_name="Lee")
    assert (again["party_id"], again["new"]) == (writer["party_id"], False)
    publisher = setup.add_credit("publisher", company_name="Lee Music")
    with pytest.raises(ToolError, match="writer or publisher"):
        setup.add_credit("lyricist")
    with pytest.raises(ToolError, match="last name"):
        setup.add_credit("writer", first_name="Cher")
    defaults = setup.set_default_shares(
        project.pk,
        [
            {"party_id": writer["party_id"], "role": "C", "share": 100},
            {"party_id": publisher["party_id"], "role": "E", "share": "100"},
        ],
    )
    assert defaults["defaults"] == ["Ann Lee (BMI) C 100%", "Lee Music E 100%"]
    assert project.default_shares.count() == 2
    with pytest.raises(ToolError, match="No credit"):
        setup.set_default_shares(project.pk, [{"party_id": 0, "role": "C"}])
    with pytest.raises(ToolError, match="percentage"):
        setup.set_default_shares(
            project.pk,
            [{"party_id": writer["party_id"], "role": "C", "share": "lots"}],
        )
    with pytest.raises(ToolError, match="add up to"):
        setup.set_default_shares(
            project.pk,
            [{"party_id": writer["party_id"], "role": "C", "share": 50}],
        )
    assert project.default_shares.count() == 2  # unchanged
    # Each write is a change set, so it can be undone.
    assert list(
        models.ChangeSet.objects.order_by("pk").values_list("project", "label")
    ) == [
        (None, "Add Ann Lee (BMI) to Credits"),
        (None, "Add Lee Music to Credits"),
        (project.pk, "Set default shares"),
    ]


def test_dates_are_iso(editor: User) -> None:
    del editor
    project = factories.ProjectFactory.create()
    setup.set_deadline(project.pk, "delivery", "2027-01-31")
    deadline = project.deadlines.get()
    assert deadline.end_date == datetime.date(2027, 1, 31)


def test_form_errors_name_the_field_or_not() -> None:
    form = forms.Form({})
    form.add_error(None, "Whole form.")
    assert common.form_errors(form) == "Whole form."
