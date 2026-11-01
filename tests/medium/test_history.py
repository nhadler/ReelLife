"""Change sets and undo (cues.services.history)."""

import datetime
from typing import Any

from django.db import connection
import pytest

from cues import models
from cues.services import history
from tests import factories

pytestmark = pytest.mark.django_db


def _project() -> models.Project:
    return factories.ProjectFactory.create()


def test_rows_inside_a_recording_are_tagged_in_order() -> None:
    project = _project()
    picture = factories.PictureFactory.create(project=project)
    with history.recording(project, "Batch") as change_set:
        cue = factories.CueFactory.create(project=project, picture=picture)
        cue.title = "Renamed"
        cue.save()
    rows = list(cue.history.order_by("history_id"))
    assert [(r.change_set_id, r.change_seq) for r in rows] == [
        (change_set.pk, 1),
        (change_set.pk, 2),
    ]
    assert str(change_set) == "Batch"


def test_rows_outside_a_recording_are_untagged() -> None:
    cue = factories.CueFactory.create()
    assert cue.history.get().change_set is None


def test_recording_rolls_back_on_error() -> None:
    project = _project()
    with pytest.raises(RuntimeError), history.recording(project, "Fails"):
        factories.CueFactory.create(project=project)
        raise RuntimeError
    assert not project.cues.exists()
    assert not models.ChangeSet.objects.exists()


def test_undo_reverts_creates_updates_and_deletes() -> None:
    project = _project()
    picture = factories.PictureFactory.create(project=project)
    kept = factories.CueFactory.create(
        project=project,
        picture=picture,
        number="1m01",
        title="Before",
        tc_in=10,
        tc_out=20,
    )
    doomed = factories.CueFactory.create(
        project=project, picture=picture, number="1m02"
    )
    doomed_pk = doomed.pk  # delete() clears it
    with history.recording(project, "Edit") as change_set:
        kept.title, kept.tc_in = "After", 15
        kept.save()
        kept.title = "After again"
        kept.save()
        doomed.delete()
        new = factories.CueFactory.create(
            project=project, picture=picture, number="1m03"
        )
        factories.CueVersionFactory.create(cue=new)

    undo_set = history.undo(change_set)

    kept.refresh_from_db()
    assert (kept.title, kept.tc_in) == ("Before", 10)
    assert project.cues.filter(pk=doomed_pk, number="1m02").exists()
    assert not project.cues.filter(number="1m03").exists()
    change_set.refresh_from_db()
    assert change_set.undone_at is not None
    assert undo_set.undo_of == change_set
    assert undo_set.label == "Undo: Edit"


def test_undo_restores_a_versions_sub_status() -> None:
    version = factories.CueVersionFactory.create()
    project = version.cue.project
    respot = project.sub_statuses.get(label="Respot")
    version.sub_status = respot
    version.save()
    with history.recording(project, "Resub") as change_set:
        version.sub_status = project.sub_statuses.get(label="Conform")
        version.save()
    history.undo(change_set)
    version.refresh_from_db()
    assert version.sub_status == respot


def test_undo_twice_is_refused() -> None:
    project = _project()
    with history.recording(project, "Once") as change_set:
        factories.CueFactory.create(project=project)
    history.undo(change_set)
    change_set.refresh_from_db()
    with pytest.raises(history.UndoError, match="already undone"):
        history.undo(change_set)


def test_undo_refuses_when_changed_afterwards() -> None:
    project = _project()
    with history.recording(project, "Import") as change_set:
        cue = factories.CueFactory.create(project=project, title="Imported")
    cue.title = "Edited later"
    cue.save()
    with pytest.raises(history.UndoError, match="changed afterwards"):
        history.undo(change_set)
    cue.refresh_from_db()
    assert cue.title == "Edited later"


def test_undo_refuses_when_others_depend_on_it() -> None:
    project = _project()
    with history.recording(project, "New reel") as change_set:
        picture = factories.PictureFactory.create(project=project)
    factories.CueFactory.create(project=project, picture=picture)
    with pytest.raises(history.UndoError, match="depend on it"):
        history.undo(change_set)
    assert models.Picture.objects.filter(pk=picture.pk).exists()


def test_undo_can_itself_be_undone() -> None:
    project = _project()
    with history.recording(project, "Add") as change_set:
        cue = factories.CueFactory.create(project=project, number="1m01")
    undo_set = history.undo(change_set)
    history.undo(undo_set)
    assert project.cues.filter(pk=cue.pk).exists()


def test_undo_steps_back_through_several_changes() -> None:
    project = _project()
    cue = factories.CueFactory.create(project=project, title="Original")
    with history.recording(project, "First") as first:
        cue.title = "First"
        cue.save()
    with history.recording(project, "Second") as second:
        cue.title = "Second"
        cue.save()
    history.undo(second)
    history.undo(first)  # the undone pair in between doesn't block this
    cue.refresh_from_db()
    assert cue.title == "Original"


def test_undoing_an_undo_counts_as_a_change_again() -> None:
    project = _project()
    cue = factories.CueFactory.create(project=project, title="A")
    with history.recording(project, "To B") as to_b:
        cue.title = "B"
        cue.save()
    with history.recording(project, "To C") as to_c:
        cue.title = "C"
        cue.save()
    undo_c = history.undo(to_c)
    history.undo(undo_c)  # redo: the title is C again
    to_b.refresh_from_db()
    with pytest.raises(history.ChangedSinceError, match="changed afterwards"):
        history.undo(to_b)
    cue.refresh_from_db()
    assert cue.title == "C"


def test_undoing_a_deletion_keeps_when_it_was_created() -> None:
    version = factories.CueVersionFactory.create()
    created = version.created_at - datetime.timedelta(days=3)
    models.CueVersion.objects.filter(pk=version.pk).update(created_at=created)
    version.refresh_from_db()
    project = version.cue.project
    with history.recording(project, "Delete") as change_set:
        version.delete()
    history.undo(change_set)
    restored = models.CueVersion.objects.get(label=version.label)
    assert restored.created_at == created
    assert (
        restored.history.order_by("-history_id").first().created_at == created
    )


def test_constraint_errors_read_plainly() -> None:
    from django.core.exceptions import ValidationError  # noqa: PLC0415 pylint: disable=import-outside-toplevel

    cue = factories.CueFactory.create(tc_in=100, tc_out=200)
    cue.tc_out = 50
    with pytest.raises(ValidationError) as error:
        cue.full_clean()
    assert "OUT can't be before IN." in error.value.messages


def _rename_and_undo(cue: models.Cue) -> None:
    with history.recording(cue.project, "Rename") as change_set:
        cue.title = "Renamed"
        cue.save()
    history.undo(change_set)


def test_undo_refuses_a_change_with_no_history_before_it() -> None:
    cue = factories.CueFactory.create(title="Before")
    cue.history.all().delete()  # e.g. made by a data migration
    with pytest.raises(
        history.UndoError, match="history of cue '.*' is incomplete"
    ):
        _rename_and_undo(cue)
    cue.refresh_from_db()
    assert cue.title == "Renamed"


def test_undo_refuses_when_the_id_was_reused() -> None:
    gone = factories.CueFactory.create(number="1m01")
    pk, project, picture = gone.pk, gone.project, gone.picture
    gone.delete()
    reused = factories.CueFactory.create(
        pk=pk, project=project, picture=picture, number="2m01"
    )
    reused.history.filter(history_type="+", number="2m01").delete()
    with pytest.raises(history.UndoError, match="incomplete"):
        _rename_and_undo(reused)
    assert models.Cue.objects.get(pk=pk).number == "2m01"


def _sequence(cursor: Any, table: str) -> int | None:
    cursor.execute("SELECT seq FROM sqlite_sequence WHERE name = %s", [table])
    row = cursor.fetchone()
    return row[0] if row else None


@pytest.mark.parametrize("counter_row", [True, False])
def test_ids_with_history_are_never_handed_out_again(counter_row: bool) -> None:
    """A migration that rebuilds a table resets its counter (0011)."""
    project = _project()
    picture = factories.PictureFactory.create(project=project)
    gone = factories.CueFactory.create(project=project, picture=picture)
    gone_pk = gone.pk
    gone.delete()
    table = models.Cue._meta.db_table  # noqa: SLF001
    with connection.cursor() as cursor:
        # What a table rebuild leaves: the counter at the largest live id.
        cursor.execute("DELETE FROM sqlite_sequence WHERE name = %s", [table])
        if counter_row:
            cursor.execute(
                "INSERT INTO sqlite_sequence (name, seq) VALUES (%s, %s)",
                [table, gone_pk - 1],
            )
        history.keep_ids_unique()
        assert _sequence(cursor, table) == gone_pk
    fresh = factories.CueFactory.create(project=project, picture=picture)
    assert fresh.pk > gone_pk


def test_keep_ids_unique_skips_tables_without_history() -> None:
    rows = getattr(models.Cue, "history")  # noqa: B009
    rows.all().delete()  # pylint: disable=no-member
    history.keep_ids_unique()  # nothing to do, nothing raised
