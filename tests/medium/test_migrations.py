"""Migrations are complete and reversible."""

import datetime
import io
from typing import Any

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
import pytest

from cues import models
from cues.services import history


@pytest.mark.django_db
def test_models_and_migrations_agree() -> None:
    call_command("makemigrations", "--check", "--dry-run", stdout=io.StringIO())


@pytest.mark.django_db(transaction=True)
def test_cues_migrations_reverse_and_reapply() -> None:
    call_command("migrate", "cues", "zero", stdout=io.StringIO())
    call_command("migrate", "cues", stdout=io.StringIO())
    # The data migrations ran again: the eight standard views are back.
    assert models.View.objects.filter(project=None).count() == 8


_BEFORE = [("cues", "0007_version_notes_fields")]


def _migrate(target: list[tuple[str, str]]) -> Any:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(target)
    return executor.loader.project_state(target).apps


@pytest.mark.django_db(transaction=True)
def test_notes_move_onto_versions_and_back() -> None:
    apps = _migrate(_BEFORE)
    project = apps.get_model("cues", "Project").objects.create(
        title="Old data", production_number="OLD1", frame_rate="24"
    )
    turnover = apps.get_model("cues", "Turnover")
    first = turnover.objects.create(
        project=project, label="c0917", received_date=datetime.date(2026, 9, 17)
    )
    latest = turnover.objects.create(
        project=project,
        label="c1030",
        received_date=datetime.date(2026, 10, 30),
    )
    picture = apps.get_model("cues", "Picture").objects.create(
        project=project, name="1"
    )
    picture_versions = apps.get_model("cues", "PictureVersion").objects
    old_cut = picture_versions.create(picture=picture, turnover=first)
    new_cut = picture_versions.create(picture=picture, turnover=latest)
    cue_model = apps.get_model("cues", "Cue")
    notes = {
        "temp": "Zimmer",
        "notes": "Bigger",
        "bpm": "92.50",
        "in_note": "On cut",
    }
    written = cue_model.objects.create(
        project=project,
        picture=picture,
        number="1m01",
        tc_in=1,
        tc_out=99,
        **notes,
    )
    version = apps.get_model("cues", "CueVersion").objects.create(
        cue=written, label="v1", written_to=old_cut
    )
    written.current_version = version
    written.save()
    cue_model.objects.create(
        project=project,
        picture=picture,
        number="1m02",
        tc_in=5,
        tc_out=50,
        temp="Pärt",
    )

    call_command("migrate", "cues", stdout=io.StringIO())
    first_cue = models.Cue.objects.get(number="1m01")
    assert first_cue.current_version is not None
    assert first_cue.current_version.temp == "Zimmer"
    assert str(first_cue.current_version.bpm) == "92.50"
    assert first_cue.current_version.written_to_id == old_cut.pk
    spotted = models.Cue.objects.get(number="1m02")
    assert spotted.current_version is not None
    assert spotted.current_version.label == "v1"
    assert spotted.current_version.temp == "Pärt"
    assert spotted.current_version.written_to_id == new_cut.pk
    assert (spotted.current_version.tc_in, spotted.current_version.tc_out) == (
        5,
        50,
    )

    apps = _migrate(_BEFORE)
    back = apps.get_model("cues", "Cue").objects.get(number="1m01")
    assert (back.temp, back.notes, back.in_note) == (
        "Zimmer",
        "Bigger",
        "On cut",
    )
    call_command("migrate", "cues", stdout=io.StringIO())


@pytest.mark.django_db(transaction=True)
def test_undo_after_the_notes_migration_keeps_the_current_version() -> None:
    """0008 wrote no history; 0011 snapshots rows so undo restores them."""
    apps = _migrate(_BEFORE)
    project = apps.get_model("cues", "Project").objects.create(
        title="Old data", production_number="OLD1", frame_rate="24"
    )
    picture = apps.get_model("cues", "Picture").objects.create(
        project=project, name="1"
    )
    cue = apps.get_model("cues", "Cue").objects.create(
        project=project, picture=picture, number="1m01", tc_in=1, tc_out=99
    )
    tag = apps.get_model("cues", "Tag").objects.create(
        project=project, name="Love", color="#cc3366"
    )
    cue.tags.add(tag)
    # What the app wrote when the cue was made: no current version yet.
    apps.get_model("cues", "HistoricalCue").objects.create(
        id=cue.pk,
        project_id=project.pk,
        picture_id=picture.pk,
        number="1m01",
        tc_in=1,
        tc_out=99,
        created_at=cue.created_at,
        updated_at=cue.updated_at,
        history_date=cue.created_at,
        history_type="+",
    )

    call_command("migrate", "cues", stdout=io.StringIO())
    project_now = models.Project.objects.get(pk=project.pk)
    with history.recording(project_now, "Rename") as change_set:
        renamed = models.Cue.objects.get(pk=cue.pk)
        renamed.title = "Renamed"
        renamed.save()
    history.undo(change_set)

    after = models.Cue.objects.get(pk=cue.pk)
    assert after.title == ""
    assert after.current_version is not None
    assert after.current_version.label == "v1"
    # 0017: the cue's tag became its current version's sub status.
    assert after.current_version.sub_status.label == "Love"  # type: ignore[union-attr]

    history_rows = getattr(models.Cue, "history")  # noqa: B009
    snapshots = history_rows.filter(  # pylint: disable=no-member
        history_change_reason="Snapshot after data migration"
    )
    assert snapshots.exists()
    call_command("migrate", "cues", "0010", stdout=io.StringIO())
    assert not snapshots.exists()
    call_command("migrate", "cues", stdout=io.StringIO())


@pytest.mark.django_db(transaction=True)
def test_existing_lists_and_credits_get_history_to_undo_to() -> None:
    """0039 snapshots the newly tracked rows, so their first edit undoes."""
    before = [("cues", "0038_day_off_per_project")]
    apps = _migrate(before)
    project = apps.get_model("cues", "Project").objects.create(
        title="Old data", production_number="OLD1", frame_rate="24"
    )
    status = apps.get_model("cues", "StatusOption").objects.create(
        project=project, label="Composed", kind="done", color="#00aa00"
    )
    party = apps.get_model("cues", "Party").objects.create(
        kind="publisher", name="Old Music"
    )

    call_command("migrate", "cues", stdout=io.StringIO())
    with history.recording(None, "Rename") as global_set:
        renamed = models.Party.objects.get(pk=party.pk)
        renamed.name = "New Music"
        renamed.save()
    with history.recording(
        models.Project.objects.get(pk=project.pk), "Edit statuses"
    ) as change_set:
        edited = models.StatusOption.objects.get(pk=status.pk)
        edited.label = "Written"
        edited.save()
    history.undo(global_set)
    history.undo(change_set)
    assert models.Party.objects.get(pk=party.pk).name == "Old Music"
    assert models.StatusOption.objects.get(pk=status.pk).label == "Composed"

    # Back past 0039: change sets without a project go, the rest stay.
    call_command("migrate", "cues", "0038", stdout=io.StringIO())
    change_sets = _migrate(before).get_model("cues", "ChangeSet").objects
    assert set(change_sets.values_list("label", flat=True)) == {
        "Edit statuses",
        "Undo: Edit statuses",
    }
    call_command("migrate", "cues", stdout=io.StringIO())


@pytest.mark.django_db(transaction=True)
def test_projects_own_standard_views_get_percent_written() -> None:
    apps = _migrate([("cues", "0045_project_color")])
    project = apps.get_model("cues", "Project").objects.create(
        title="Old views", production_number="OLD2", frame_rate="24"
    )
    views = apps.get_model("cues", "View").objects
    mine = views.create(
        project=project,
        name="Standard",
        columns=[{"id": "number"}, {"id": "status", "width": 140}],
    )
    other = views.create(
        project=project, name="Standard 2", columns=[{"id": "status"}]
    )
    _migrate([("cues", "0046_views_percent_written")])
    assert models.View.objects.get(pk=mine.pk).columns == [
        {"id": "number"},
        {"id": "status", "width": 140},
        {"id": "percent_written"},
    ]
    # Only views named Standard.
    assert models.View.objects.get(pk=other.pk).columns == [{"id": "status"}]
    _migrate([("cues", "0045_project_color")])
    assert models.View.objects.get(pk=mine.pk).columns == [
        {"id": "number"},
        {"id": "status", "width": 140},
    ]
    call_command("migrate", "cues", stdout=io.StringIO())
