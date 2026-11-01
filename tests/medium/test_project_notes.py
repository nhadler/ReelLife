"""Project notes: general and per kind (Oct 31)."""

from collections.abc import Callable
import re

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


def _save(
    client: Client,
    project: models.Project,
    kind: str,
    text: str,
    updated_at: str = "",
) -> str:
    return client.post(
        reverse("cues:project_note_save", args=[project.pk, kind]),
        {"text": text, "updated_at": updated_at},
    ).content.decode()


def test_every_kind_on_the_page_and_panel(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    page = editor.get(reverse("cues:project_notes", args=[project.pk]))
    html = page.content.decode()
    for kind in models.NoteKind:
        assert f'id="note-{kind.value}"' in html
        assert kind.label in html
    panel = editor.get(
        reverse("cues:project_notes_panel", args=[project.pk])
    ).content.decode()
    assert "data-side-panel-close" in panel
    assert panel.count('class="project-note"') == len(models.NoteKind)


def test_one_kind_in_a_dialog(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    url = reverse("cues:project_notes_panel", args=[project.pk])
    html = editor.get(url, {"kind": "spotting"}).content.decode()
    assert 'id="dialog-title"' in html
    assert html.count('class="project-note"') == 1
    assert editor.get(url, {"kind": "nope"}).status_code == 404


def test_saving_editing_stale_and_undo(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    html = _save(editor, project, "general", "Director prefers strings.")
    assert "Saved." in html
    note = project.notes.get(kind="general")
    assert note.text == "Director prefers strings."
    stamp = note.updated_at.isoformat()
    _save(editor, project, "general", "Strings, no brass.", stamp)
    note.refresh_from_db()
    assert note.text == "Strings, no brass."
    # Shown before that save: refused, but the draft stays in the box
    # with the saved text under it, and saving again takes the draft.
    html = _save(editor, project, "general", "Mine", stamp)
    assert "changed elsewhere" in html
    assert re.search(r"<textarea[^>]*>Mine</textarea>", html)
    assert re.search(r"project-note__theirs.*Strings, no brass\.", html, re.S)
    note.refresh_from_db()
    assert note.text == "Strings, no brass."
    _save(editor, project, "general", "Mine", note.updated_at.isoformat())
    note.refresh_from_db()
    assert note.text == "Mine"
    # Saving the same text records nothing.
    count = models.ChangeSet.objects.count()
    note.refresh_from_db()
    _save(editor, project, "general", note.text, note.updated_at.isoformat())
    assert models.ChangeSet.objects.count() == count
    history.undo(models.ChangeSet.objects.filter(project=project).latest("pk"))
    note.refresh_from_db()
    assert note.text == "Strings, no brass."


def test_viewers_read_but_cannot_save(
    role_client: Callable[[Role], Client],
) -> None:
    project = factories.ProjectFactory.create()
    models.ProjectNote.objects.create(
        project=project, kind="mix", text="Stems by Friday."
    )
    viewer = role_client(Role.VIEWER)
    html = viewer.get(
        reverse("cues:project_notes", args=[project.pk])
    ).content.decode()
    assert "Stems by Friday." in html
    assert "<textarea" not in html
    assert "No notes yet." in html
    response = viewer.post(
        reverse("cues:project_note_save", args=[project.pk, "mix"]),
        {"text": "x", "updated_at": ""},
    )
    assert response.status_code == 403
    assert str(project.notes.get()) == "Mix Notes"


def test_bad_kind_is_not_found(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    url = reverse("cues:project_note_save", args=[project.pk, "nope"])
    assert editor.post(url, {"text": "x"}).status_code == 404


def test_ways_in(editor: Client) -> None:
    cue = factories.CueFactory.create()
    project = cue.project
    overview = editor.get(
        reverse("cues:project", args=[project.pk])
    ).content.decode()
    assert 'id="notes-toggle"' in overview
    assert 'id="side-panel"' in overview
    picture = editor.get(
        reverse("cues:picture", args=[project.pk])
    ).content.decode()
    assert reverse("cues:project_notes", args=[project.pk]) in picture
