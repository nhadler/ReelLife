"""A project's Trash: deleted cues and Restore (cues.services.trash)."""

from collections.abc import Callable
from decimal import Decimal
from html import unescape

from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from cues import defaults
from cues import models
from cues.services import authoring
from cues.services import history
from cues.services import tools
from cues.services import trash
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    return factories.ProjectFactory.create(title="The Voyage")


def _cue(project: models.Project, number: str) -> models.Cue:
    """A cue with v1 and v2 (current: v1), a share, a theme, an instrument."""
    cue = factories.CueFactory.create(
        project=project, number=number, title=f"Title {number}"
    )
    first = factories.CueVersionFactory.create(cue=cue, label="v1")
    factories.CueVersionFactory.create(cue=cue, label="v2")
    cue.current_version = first
    cue.save()
    party = models.Party.objects.create(kind="publisher", name="Sea Music")
    models.CueShare.objects.create(
        cue=cue, party=party, role="E", share=Decimal(100)
    )
    theme = models.ThemeOption.objects.create(
        project=project, name=f"Theme {number}", color="#aa0000"
    )
    models.VersionTheme.objects.create(version=first, theme=theme, seconds=9)
    strings = models.InstrumentOption.objects.create(
        project=project, name=f"Strings {number}", color="#00aa00"
    )
    models.CueInstrument.objects.create(cue=cue, instrument=strings)
    return cue


def _trash(project: models.Project) -> list[trash.Deleted]:
    return trash.entries(project, trash.deletions(project))


def _only(entries: list[trash.Deleted]) -> trash.Deleted:
    assert len(entries) == 1
    return entries[0]


def _url(name: str, project: models.Project, *args: int) -> str:
    return reverse(f"cues:{name}", args=[project.pk, *args])


def test_a_deleted_cue_comes_back_with_everything_it_had(
    project: models.Project,
) -> None:
    cue = _cue(project, "1m05")
    user = factories.UserFactory.create(username="composer")
    pk = cue.pk
    authoring.delete_cue(cue, user=user)
    entry = _only(_trash(project))
    assert (entry.number, entry.title) == ("1m05", "Title 1m05")
    assert (entry.by, entry.user, entry.taken) == (
        "Delete cue 1m05",
        "composer",
        False,
    )

    back = trash.restore(project, pk, user)

    assert back.number == "1m05"
    assert back.current_version is not None
    assert back.current_version.label == "v1"
    assert sorted(back.versions.values_list("label", flat=True)) == [
        "v1",
        "v2",
    ]
    assert back.shares.get().party.name == "Sea Music"
    assert back.instrumentation.get().instrument.name == "Strings 1m05"
    assert back.current_version.themes.get().seconds == 9
    assert not _trash(project)
    change_set = models.ChangeSet.objects.filter(project=project).latest("pk")
    assert (change_set.label, change_set.user) == ("Restore 1m05", user)
    # Undoing the restore puts it back in the Trash.
    history.undo(change_set)
    assert [e.number for e in _trash(project)] == ["1m05"]


def test_restore_works_when_the_deletion_cant_be_undone(
    project: models.Project,
) -> None:
    first, second = _cue(project, "1m01"), _cue(project, "1m02")
    first_pk = first.pk
    tools.delete([first, second])
    factories.CueFactory.create(project=project, number="1m02")
    with pytest.raises(history.UndoError):
        history.undo(models.ChangeSet.objects.get(label="Delete 2 cues"))
    assert trash.restore(project, first_pk).number == "1m01"
    assert [e.number for e in _trash(project)] == ["1m02"]


def test_a_taken_number_is_refused_or_given_a_suffix(
    project: models.Project,
) -> None:
    project.cue_number_pattern = defaults.ANY_CUE_NUMBER_PATTERN
    project.save()
    cue = _cue(project, "1m05")
    pk = cue.pk
    authoring.delete_cue(cue)
    factories.CueFactory.create(project=project, number="1M05")
    factories.CueFactory.create(project=project, number="1m05r")
    entry = _only(_trash(project))
    assert (entry.taken, entry.suggestion) == (True, "1m05r2")
    with pytest.raises(
        trash.TrashError,
        match="Another active cue is 1m05 now; restore this one as 1m05r2",
    ):
        trash.restore(project, pk)
    back = trash.restore(project, pk, renumber=True)
    assert back.number == "1m05r2"
    label = models.ChangeSet.objects.filter(project=project).latest("pk").label
    assert label == "Restore 1m05r2"


def test_an_inactive_cue_keeps_its_number(project: models.Project) -> None:
    cue = factories.CueFactory.create(project=project, number="1m05")
    cue.active = False
    cue.save()
    pk = cue.pk
    authoring.delete_cue(cue)
    factories.CueFactory.create(project=project, number="1m05")
    assert not _trash(project)[0].taken
    assert trash.restore(project, pk).number == "1m05"


def test_something_deleted_since_is_named(project: models.Project) -> None:
    status = factories.StatusOptionFactory.create(project=project)
    cue = factories.CueFactory.create(project=project, status=status)
    pk, number = cue.pk, cue.number
    authoring.delete_cue(cue)
    status.delete()
    with pytest.raises(
        trash.TrashError,
        match=f"{number} can't be restored: a status it uses was deleted",
    ):
        trash.restore(project, pk)
    assert not models.Cue.objects.filter(pk=pk).exists()


def test_a_credit_deleted_since_is_named(project: models.Project) -> None:
    cue = _cue(project, "1m05")
    pk = cue.pk
    authoring.delete_cue(cue)
    models.Party.objects.all().delete()
    with pytest.raises(trash.TrashError, match="writer or publisher it uses"):
        trash.restore(project, pk)


def test_a_cue_restored_already_isnt_in_the_trash(
    project: models.Project,
) -> None:
    cue = factories.CueFactory.create(project=project)
    pk = cue.pk
    authoring.delete_cue(cue)
    trash.restore(project, pk)
    with pytest.raises(trash.TrashError, match="isn't in the Trash any more"):
        trash.restore(project, pk)


def test_a_cue_deleted_in_the_admin_comes_back_too(
    project: models.Project,
) -> None:
    cue = _cue(project, "1m05")
    pk = cue.pk
    cue.delete()  # no change set, as the admin does it
    entry = _only(_trash(project))
    assert (entry.by, entry.user) == ("Deleted", "")
    back = trash.restore(project, pk)
    assert back.versions.count() == 2
    assert back.current_version is not None
    assert back.current_version.label == "v1"


@pytest.mark.parametrize("in_a_change_set", [True, False])
def test_the_newest_version_is_current_when_none_was(
    project: models.Project, in_a_change_set: bool
) -> None:
    cue = factories.CueFactory.create(project=project)
    factories.CueVersionFactory.create(cue=cue, label="v1")
    factories.CueVersionFactory.create(cue=cue, label="v2")
    pk = cue.pk
    if in_a_change_set:
        authoring.delete_cue(cue)
    else:
        cue.delete()
    back = trash.restore(project, pk)
    assert back.current_version is not None
    assert back.current_version.label == "v2"


def test_a_cue_without_versions_comes_back(project: models.Project) -> None:
    cue = factories.CueFactory.create(project=project)
    pk = cue.pk
    with history.recording(project, "Remove"):
        cue.delete()
    assert trash.restore(project, pk).current_version is None


# The pages ------------------------------------------------------------------


def test_the_trash_page_restores(
    editor: Client, project: models.Project
) -> None:
    cue = _cue(project, "1m05")
    pk = cue.pk
    authoring.delete_cue(cue)
    page = unescape(editor.get(_url("trash", project)).content.decode())
    assert "Delete cue 1m05" in page and "Title 1m05" in page
    response = editor.post(_url("trash_restore", project, pk), {"page": "1"})
    assert response["Location"] == _url("trash", project) + "?page=1"
    page = unescape(editor.get(_url("trash", project)).content.decode())
    assert "Restored 1m05." in page
    assert "The Trash is empty." in page


def test_a_suffix_that_doesnt_fit_isnt_offered(
    editor: Client, project: models.Project
) -> None:
    cue = _cue(project, "1m05")
    pk = cue.pk
    authoring.delete_cue(cue)
    factories.CueFactory.create(project=project, number="1m05")
    factories.CueFactory.create(project=project, number="1m05r")
    assert not _only(_trash(project)).suggestion  # 1m05r2 doesn't fit
    page = unescape(editor.get(_url("trash", project)).content.decode())
    assert "renumber that cue to restore this one" in page
    assert "Restore as" not in page
    with pytest.raises(trash.TrashError, match="renumber that 1m05 first"):
        trash.restore(project, pk, renumber=True)


def test_the_trash_page_offers_a_suffix_and_says_why_it_cant(
    editor: Client, project: models.Project
) -> None:
    cue = _cue(project, "1m05")
    pk = cue.pk
    authoring.delete_cue(cue)
    factories.CueFactory.create(project=project, number="1m05")
    page = unescape(editor.get(_url("trash", project)).content.decode())
    assert "Restore as 1m05r" in page
    response = editor.post(_url("trash_restore", project, pk), {"page": "x"})
    assert response["Location"] == _url("trash", project)
    page = unescape(editor.get(_url("trash", project)).content.decode())
    assert "Another active cue is 1m05 now" in page
    editor.post(_url("trash_restore", project, pk), {"renumber": "1"})
    assert models.Cue.objects.get(pk=pk).number == "1m05r"


def test_viewers_see_the_trash_without_restore(
    role_client: Callable[[Role], Client], project: models.Project
) -> None:
    cue = factories.CueFactory.create(project=project, number="1m05")
    authoring.delete_cue(cue)
    viewer = role_client(Role.VIEWER)
    page = unescape(viewer.get(_url("trash", project)).content.decode())
    assert "1m05" in page
    assert "Restore" not in page.split("<main", 1)[-1].split("</table>")[0]
