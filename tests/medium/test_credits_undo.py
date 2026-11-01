"""Undo on the Credits page: Undo Last Change and Recent Changes."""

from collections.abc import Callable
from decimal import Decimal
from html import unescape

from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from cues import models
from cues.services import history
from tests import factories

pytestmark = pytest.mark.django_db

CREDITS = reverse("cues:credits")
UNDO = reverse("cues:credits_undo")


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


def _add_writer(client: Client, first: str, last: str) -> None:
    data = {
        "writers-TOTAL_FORMS": "1",
        "writers-INITIAL_FORMS": "0",
        "writers-0-first_name": first,
        "writers-0-last_name": last,
        "publishers-TOTAL_FORMS": "0",
        "publishers-INITIAL_FORMS": "0",
    }
    assert client.post(CREDITS, data)["Location"] == CREDITS


def _page(client: Client) -> str:
    return unescape(client.get(CREDITS).content.decode())


def test_a_credits_save_is_a_change_set_without_a_project(
    editor: Client,
) -> None:
    _add_writer(editor, "Jane", "Doe")
    change_set = models.ChangeSet.objects.get()
    assert change_set.project is None
    assert change_set.label == "Edit credits: add Jane Doe"
    page = _page(editor)
    assert f'action="{UNDO}"' in page
    assert "Edit credits: add Jane Doe" in page


def test_saving_unchanged_credits_records_nothing(editor: Client) -> None:
    data = {
        "writers-TOTAL_FORMS": "0",
        "writers-INITIAL_FORMS": "0",
        "publishers-TOTAL_FORMS": "0",
        "publishers-INITIAL_FORMS": "0",
    }
    editor.post(CREDITS, data)
    assert not models.ChangeSet.objects.exists()
    assert "No changes yet." in _page(editor)


def test_undo_last_change_takes_back_the_users_latest(editor: Client) -> None:
    _add_writer(editor, "Jane", "Doe")
    _add_writer(editor, "Ann", "Lee")
    assert editor.post(UNDO)["Location"] == CREDITS
    names = set(models.Party.objects.values_list("name", flat=True))
    assert names == {"Jane Doe"}
    page = _page(editor)
    assert "Undid Edit credits: add Ann Lee." in page
    assert "Undone" in page
    editor.post(UNDO)
    assert not models.Party.objects.exists()
    editor.post(UNDO)
    assert "Nothing to undo." in _page(editor)
    assert f'action="{UNDO}"' not in _page(editor)


def test_undo_last_change_leaves_other_users_changes(
    editor: Client, role_client: Callable[[Role], Client]
) -> None:
    _add_writer(role_client(Role.ADMIN), "Ann", "Lee")
    editor.post(UNDO)
    assert models.Party.objects.filter(name="Ann Lee").exists()


def test_undo_of_a_writer_now_credited_is_refused(editor: Client) -> None:
    _add_writer(editor, "Jane", "Doe")
    models.CueShare.objects.create(
        cue=factories.CueFactory.create(),
        party=models.Party.objects.get(),
        role="C",
        share=Decimal(100),
    )
    editor.post(UNDO)
    page = _page(editor)
    assert (
        "Writer or publisher 'Jane Doe' is used by 1 credit now, so it "
        "can't be taken away." in page
    )
    assert models.Party.objects.exists()


def test_undo_of_a_credit_changed_since_is_refused(editor: Client) -> None:
    _add_writer(editor, "Jane", "Doe")
    party = models.Party.objects.get()
    with history.recording(None, "Rename"):
        party.last_name = "Smith"
        party.save()
    models.ChangeSet.objects.filter(label="Rename").update(user=None)
    editor.post(UNDO)
    assert "Writer or publisher 'Jane Smith' was changed afterwards" in (
        _page(editor)
    )


def test_viewers_see_recent_changes_without_undo(
    editor: Client, role_client: Callable[[Role], Client]
) -> None:
    _add_writer(editor, "Jane", "Doe")
    page = unescape(role_client(Role.VIEWER).get(CREDITS).content.decode())
    assert "Edit credits: add Jane Doe" in page
    assert f'action="{UNDO}"' not in page
