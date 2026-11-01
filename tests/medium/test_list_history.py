"""Undo for the Credits list and the project lists (history.TRACKED).

Parties, default shares and the status, sub status, theme, instrument
and department lists are tracked, so their edits undo like any other;
undo refuses to take away an entry something uses now, or to put back
one that clashes or points at something deleted since.
"""

from decimal import Decimal

import pytest

from cues import models
from cues.api import edits
from cues.services import history
from tests import factories

pytestmark = pytest.mark.django_db


def _writer(last_name: str = "Doe") -> models.Party:
    return models.Party.objects.create(
        kind=models.Party.Kind.WRITER, first_name="Jane", last_name=last_name
    )


def test_a_change_to_the_credits_list_belongs_to_no_project() -> None:
    with history.recording(None, "Add Jane Doe") as change_set:
        party = _writer()
    assert change_set.project is None
    history.undo(change_set)
    assert not models.Party.objects.filter(pk=party.pk).exists()


def test_a_new_projects_lists_can_be_undone_from_the_first_edit() -> None:
    project = factories.ProjectFactory.create()
    status = project.statuses.get(label="Composed")
    with history.recording(project, "Edit statuses") as change_set:
        status.label = "Written"
        status.save()
    history.undo(change_set)
    status.refresh_from_db()
    assert status.label == "Composed"


def test_undoing_a_new_status_that_cues_now_use_is_refused() -> None:
    project = factories.ProjectFactory.create()
    with history.recording(project, "Edit statuses") as change_set:
        status = factories.StatusOptionFactory.create(
            project=project, label="Mixing"
        )
    factories.CueFactory.create(project=project, status=status)
    with pytest.raises(
        history.UndoError,
        match="Status 'Mixing' is used by 1 cue now, so it can't be taken",
    ):
        history.undo(change_set)
    assert models.StatusOption.objects.filter(pk=status.pk).exists()


def test_undoing_a_new_party_now_credited_is_refused() -> None:
    with history.recording(None, "Add Jane Doe") as change_set:
        party = _writer()
    cue = factories.CueFactory.create()
    models.CueShare.objects.create(
        cue=cue, party=party, role="C", share=Decimal(100)
    )
    models.DefaultShare.objects.create(
        project=cue.project, party=party, role="C", share=Decimal(100)
    )
    with pytest.raises(history.UndoError, match="used by 2 credits now"):
        history.undo(change_set)


def test_undoing_a_typed_sub_status_removes_it_from_the_list() -> None:
    version = factories.CueVersionFactory.create()
    cue = version.cue
    cue.current_version = version
    cue.save()
    edits.apply(cue, "sub_status", "Waiting on Picture")
    project = cue.project
    added = project.sub_statuses.get(label="Waiting on Picture")
    history.undo(models.ChangeSet.objects.filter(project=project).latest("pk"))
    version.refresh_from_db()
    assert version.sub_status is None
    assert not models.SubStatusOption.objects.filter(pk=added.pk).exists()


def test_a_typed_sub_status_another_version_took_up_stays() -> None:
    version = factories.CueVersionFactory.create()
    cue = version.cue
    cue.current_version = version
    cue.save()
    edits.apply(cue, "sub_status", "Waiting")
    typed = models.ChangeSet.objects.filter(project=cue.project).latest("pk")
    other = factories.CueVersionFactory.create(
        cue=factories.CueFactory.create(project=cue.project)
    )
    other.sub_status = cue.project.sub_statuses.get(label="Waiting")
    other.save()
    with pytest.raises(history.UndoError, match="used by 1 version now"):
        history.undo(typed)
    version.refresh_from_db()
    assert version.sub_status is not None  # all or nothing


def test_renaming_back_a_department_status_cues_took_up_is_refused() -> None:
    project = factories.ProjectFactory.create()
    option = project.department_statuses.get(field="mix_status", name="To Mix")
    with history.recording(project, "Rename To Mix to Waiting") as change_set:
        option.name = "Waiting"
        option.save()
    factories.CueFactory.create(project=project, mix_status="Waiting")
    with pytest.raises(
        history.UndoError,
        match="1 cue uses 'Waiting' now, so it can't go back to 'To Mix'",
    ):
        history.undo(change_set)


def test_a_department_status_change_keeping_its_name_undoes() -> None:
    project = factories.ProjectFactory.create()
    option = project.department_statuses.get(field="mix_status", name="To Mix")
    factories.CueFactory.create(project=project, mix_status="To Mix")
    with history.recording(project, "Recolour") as change_set:
        option.color = "#123456"
        option.save()
    history.undo(change_set)
    option.refresh_from_db()
    assert option.color != "#123456"


def test_putting_back_an_entry_whose_name_is_taken_is_refused() -> None:
    project = factories.ProjectFactory.create()
    theme = models.ThemeOption.objects.create(
        project=project, name="Love", color="#aa0000"
    )
    with history.recording(project, "Edit lists") as change_set:
        theme.delete()
    models.ThemeOption.objects.create(
        project=project, name="love", color="#00aa00"
    )
    with pytest.raises(
        history.UndoError,
        match="Theme 'Love' can't be put back: another one has its name",
    ):
        history.undo(change_set)


def test_putting_back_a_cue_whose_status_was_deleted_is_refused() -> None:
    project = factories.ProjectFactory.create()
    status = factories.StatusOptionFactory.create(project=project)
    cue = factories.CueFactory.create(project=project, status=status)
    with history.recording(project, "Delete cue") as change_set:
        cue.delete()
    status.delete()
    with pytest.raises(
        history.UndoError,
        match="A cue can't be put back: the status it uses was deleted",
    ):
        history.undo(change_set)


def test_going_back_to_a_deleted_sub_status_is_refused() -> None:
    version = factories.CueVersionFactory.create()
    sub_status = version.cue.project.sub_statuses.get(label="Respot")
    version.sub_status = sub_status
    version.save()
    with history.recording(version.cue.project, "Clear") as change_set:
        version.sub_status = None
        version.save()
    sub_status.delete()
    with pytest.raises(history.UndoError, match="the sub status it uses"):
        history.undo(change_set)


def test_undoing_a_deleted_theme_puts_back_the_versions_using_it() -> None:
    version = factories.CueVersionFactory.create()
    project = version.cue.project
    theme = models.ThemeOption.objects.create(
        project=project, name="Love", color="#aa0000"
    )
    models.VersionTheme.objects.create(version=version, theme=theme, seconds=4)
    with history.recording(project, "Edit lists: delete Love") as change_set:
        theme.delete()  # takes the version's link with it
    assert not version.themes.exists()
    history.undo(change_set)
    assert version.themes.get().theme.name == "Love"
    assert version.themes.get().seconds == 4


def test_a_default_share_edit_undoes() -> None:
    project = factories.ProjectFactory.create()
    party = _writer()
    with history.recording(project, "Edit cue sheet settings") as change_set:
        models.DefaultShare.objects.create(
            project=project, party=party, role="C", share=Decimal(100)
        )
    history.undo(change_set)
    assert not project.default_shares.exists()
