"""Change sets as list entries (cues.services.activity)."""

import pytest

from cues import models
from cues.services import activity
from cues.services import history
from tests import factories

pytestmark = pytest.mark.django_db


def _entry(change_set: models.ChangeSet) -> activity.Entry:
    return activity.entries([change_set])[0]


def test_an_entry_counts_the_cues_it_touched() -> None:
    project = factories.ProjectFactory.create()
    theme = models.ThemeOption.objects.create(
        project=project, name="Love", color="#aa0000"
    )
    first, second, third = factories.CueFactory.create_batch(3, project=project)
    version = factories.CueVersionFactory.create(cue=third)
    with history.recording(project, "Import") as change_set:
        first.title = "One"
        first.save()
        factories.CueVersionFactory.create(cue=second)
        models.VersionTheme.objects.create(version=version, theme=theme)
        models.StatusOption.objects.create(
            project=project, label="New", kind="to_do", color="#000000"
        )
    entry = _entry(change_set)
    assert entry.cues == 3
    assert (entry.label, entry.when) == ("Import", change_set.created_at)
    assert (entry.action, entry.blocked, entry.undone) == ("undo", False, False)


def test_a_later_change_blocks_undo_until_it_is_undone() -> None:
    cue = factories.CueFactory.create()
    with history.recording(cue.project, "First") as first:
        cue.title = "One"
        cue.save()
    with history.recording(cue.project, "Second") as second:
        cue.title = "Two"
        cue.save()
    assert _entry(first).blocked
    assert _entry(first).action == ""
    history.undo(second)
    assert _entry(first).action == "undo"


def test_an_untracked_later_change_blocks_undo() -> None:
    cue = factories.CueFactory.create()
    with history.recording(cue.project, "First") as first:
        cue.title = "One"
        cue.save()
    cue.title = "In the admin"
    cue.save()
    assert _entry(first).blocked


def test_undo_entries_offer_redo_and_redo_entries_undo() -> None:
    cue = factories.CueFactory.create()
    with history.recording(cue.project, "Rename") as rename:
        cue.title = "One"
        cue.save()
    undo = history.undo(rename)
    rename.refresh_from_db()
    entries = activity.entries([undo, rename])
    assert [(e.action, e.undone) for e in entries] == [
        ("redo", False),
        ("", True),
    ]
    redo = history.undo(undo)
    assert _entry(redo).action == "undo"


def test_user_names() -> None:
    assert activity.user_name(None) == ""
    user = factories.UserFactory.create(username="nick")
    assert activity.user_name(user) == "nick"
    user.first_name, user.last_name = "Nick", "Hadler"
    assert activity.user_name(user) == "Nick Hadler"
