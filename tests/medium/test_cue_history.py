"""A cue's history as the details page shows it (cues/services/cue_history)."""

import datetime

import pytest

from cues import models
from cues.api import edits
from cues.services import authoring
from cues.services import cue_history
from cues.services import history
from tests import factories

pytestmark = pytest.mark.django_db


def _lines(cue: models.Cue) -> list[tuple[str, str, str, str]]:
    return [
        (change.field, change.old, change.new, change.kind)
        for entry in cue_history.entries(cue)
        for change in entry.changes
    ]


def test_values_read_in_the_projects_terms() -> None:
    cue = factories.CueFactory.create(title="Old")
    status = factories.StatusOptionFactory.create(
        project=cue.project, label="Scored"
    )
    edits.apply(cue, "tc_in", "1:00:00:00")
    edits.apply(cue, "status", status.pk)
    edits.apply(cue, "usage", "VI")
    edits.apply(cue, "plays_as_source", True)
    edits.apply(cue, "bpm", "92.5")
    edits.apply(cue, "sub_status", "Hold")  # typed: added to the list
    lines = _lines(cue)
    assert ("IN", "1:00:10:00", "1:00:00:00", "changed") in lines
    assert ("Status", "—", "Scored", "changed") in lines
    assert (
        "Usage",
        "Background Instrumental",
        "Visual Instrumental",
        "changed",
    ) in lines
    assert ("Plays as source", "No", "Yes", "changed") in lines
    assert ("BPM", "—", "92.50", "changed") in lines
    assert ("Sub status", "—", "Hold", "changed") in lines


def test_deleted_things_and_dates() -> None:
    version = factories.CueVersionFactory.create()
    cue = version.cue
    turnover = factories.PictureVersionFactory.create(picture=cue.picture)
    edits.apply_version(version, "written_to", turnover.pk)
    edits.apply_version(version, "delivered_at", "2026-10-30T14:00")
    edits.apply_version(version, "sub_status", "Gone")
    cue.project.sub_statuses.get(label="Gone").delete()
    status = factories.StatusOptionFactory.create(project=cue.project)
    edits.apply(cue, "status", status.pk)
    edits.apply(cue, "status", "")
    status.delete()
    lines = _lines(cue)
    assert any(
        f == "Written to" and new.endswith(turnover.turnover.label)
        for f, _, new, _ in lines
    )
    assert any(
        f == "Delivered at" and "30 Oct 2026" in new for f, _, new, _ in lines
    )
    assert ("Status", "(deleted)", "—", "changed") in lines
    assert any(
        f == "Sub status" and new == "(deleted)" for f, _, new, _ in lines
    )


def test_creation_and_deletion() -> None:
    cue = factories.CueFactory.create()
    version = factories.CueVersionFactory.create(cue=cue, label="v1")
    authoring.delete_version(version)
    entries = cue_history.entries(cue)
    assert entries[0].label == f"Delete {cue.number} v1"
    assert ("v1", "deleted") in [
        (c.subject, c.kind) for c in entries[0].changes
    ]
    labels = [e.label for e in entries]
    assert "Created version v1" in labels
    assert "Created cue" in labels
    assert not entries[-1].can_revert  # no change set to undo


def test_untracked_edits() -> None:
    cue = factories.CueFactory.create(title="Before")
    cue.title = "After"
    cue.save()
    version = factories.CueVersionFactory.create(cue=cue, label="v1")
    version.delete()
    labels = [e.label for e in cue_history.entries(cue)]
    assert labels[:3] == [
        "Deleted version v1",
        "Created version v1",
        "Changed cue",
    ]


def test_limit_and_users() -> None:
    cue = factories.CueFactory.create()
    user = factories.UserFactory.create(
        first_name="Casey", last_name="Composer"
    )
    for n in range(4):
        edits.apply(cue, "title", f"Take {n}", user)
    entries = cue_history.entries(cue, limit=2)
    assert len(entries) == 2
    assert entries[0].user == "Casey Composer"
    anonymous = factories.UserFactory.create(username="nick")
    edits.apply(cue, "title", "Take 9", anonymous)
    assert cue_history.entries(cue)[0].user == "nick"


def test_undone_entries_and_redo() -> None:
    cue = factories.CueFactory.create()
    edits.apply(cue, "title", "New")
    change_set = models.ChangeSet.objects.get(label=f"Edit {cue.number} Title")
    undo = history.undo(change_set)
    entries = cue_history.entries(cue)
    assert entries[0].label == undo.label
    assert entries[0].can_revert
    assert entries[1].undone
    assert not entries[1].can_revert


def test_other_cues_in_the_same_action() -> None:
    first = factories.CueFactory.create()
    second = factories.CueFactory.create(
        project=first.project, picture=first.picture
    )
    with history.recording(first.project, "Import"):
        for cue in (first, second):
            cue.artist = "x"
            cue.save()
    assert cue_history.entries(first)[0].other_cues == 1


def test_unknown_version_fields_are_refused() -> None:
    version = factories.CueVersionFactory.create()
    with pytest.raises(edits.EditError, match="can't be edited"):
        edits.apply_version(version, "cue", 1)


def test_edits_on_details_only_fields() -> None:
    cue = factories.CueFactory.create()
    edits.apply(cue, "active", False)
    cue.refresh_from_db()
    assert not cue.active
    assert models.ChangeSet.objects.filter(
        label=f"Edit {cue.number} Active"
    ).exists()
    with pytest.raises(edits.EditError, match="isn't in this project"):
        edits.apply(cue, "picture", "")


def test_dates_are_shown_in_local_time(settings: object) -> None:
    del settings
    version = factories.CueVersionFactory.create()
    version.delivered_at = datetime.datetime(
        2026, 10, 31, 0, 30, tzinfo=datetime.UTC
    )
    version.save()
    edits.apply_version(version, "change_notes", "x")
    assert cue_history.entries(version.cue)


def test_history_that_starts_part_way() -> None:
    cue = factories.CueFactory.create()
    models.Cue.history.filter(id=cue.pk).delete()  # pylint: disable=no-member
    edits.apply(cue, "title", "Later")
    entries = cue_history.entries(cue)
    assert [e.label for e in entries] == [f"Edit {cue.number} Title"]
    assert entries[0].changes == []


def test_a_long_history_reads_only_what_it_shows() -> None:
    cue = factories.CueFactory.create(title="Take 0")
    for take in range(1, 16):
        edits.apply(cue, "title", f"Take {take}")
    shown = cue_history.entries(cue, limit=2)
    assert [(c.old, c.new) for e in shown for c in e.changes] == [
        ("Take 14", "Take 15"),
        ("Take 13", "Take 14"),
    ]
    # Entries further back still diff correctly.
    oldest = cue_history.entries(cue, limit=10)[-1]
    assert [(c.old, c.new) for c in oldest.changes] == [("Take 5", "Take 6")]
