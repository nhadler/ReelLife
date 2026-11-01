"""Seeds the eight standard views, in order.

Decided Oct 31. Overview is renamed Standard in place, so a browser that
remembers it keeps it; Orchestration, Recording, Mixing and Viewer are
new. Frozen copy of cues.defaults.global_views() at the time of writing.
"""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

VIEWS = [
    (
        "Standard",
        "tc_in",
        (
            "status",
            "number",
            "title",
            "version",
            "tags",
            "tc_in",
            "tc_out",
            "duration",
            "written_to",
            "current_picture",
            "notes",
            "priority",
            "updated_at",
        ),
    ),
    (
        "Spotting",
        "tc_in",
        (
            "status",
            "number",
            "title",
            "version",
            "tags",
            "seq_start",
            "tc_in",
            "tc_out",
            "duration",
            "written_to",
            "current_picture",
            "usage_theme",
            "usage",
            "scene",
            "scene_description",
            "temp",
            "in_note",
            "out_note",
            "spotting_notes",
            "notes",
            "priority",
            "cue_type",
            "plays_as_source",
            "interrupted_use",
            "updated_at",
        ),
    ),
    (
        "Conform",
        "tc_in",
        (
            "status",
            "number",
            "title",
            "version",
            "tags",
            "seq_start",
            "tc_in",
            "tc_out",
            "duration",
            "written_to",
            "current_picture",
            "start_offset",
            "duration_difference",
            "version_seq_start",
            "version_in",
            "version_out",
            "version_duration",
            "change_notes",
            "updated_at",
        ),
    ),
    (
        "Orchestration",
        "tc_in",
        (
            "status",
            "number",
            "title",
            "version",
            "tags",
            "seq_start",
            "tc_in",
            "tc_out",
            "duration",
            "written_to",
            "current_picture",
            "key",
            "bpm",
            "meter",
            "orchestration_status",
            "score",
            "parts",
            "printed",
            "orchestration_version",
            "orchestration_notes",
            "recording_status",
            "recording_notes",
            "priority",
            "updated_at",
        ),
    ),
    (
        "Recording",
        "tc_in",
        (
            "status",
            "number",
            "title",
            "version",
            "tags",
            "seq_start",
            "tc_in",
            "tc_out",
            "duration",
            "written_to",
            "current_picture",
            "orchestration_status",
            "score",
            "parts",
            "printed",
            "orchestration_version",
            "orchestration_notes",
            "recording_status",
            "clicks",
            "prelays",
            "recording_notes",
            "mix_status",
            "mix_notes",
            "priority",
            "updated_at",
        ),
    ),
    (
        "Mixing",
        "tc_in",
        (
            "status",
            "number",
            "title",
            "version",
            "tags",
            "seq_start",
            "tc_in",
            "tc_out",
            "duration",
            "written_to",
            "current_picture",
            "mix_status",
            "mix_version",
            "mix_notes",
            "priority",
            "updated_at",
        ),
    ),
    (
        "Viewer",
        "tc_in",
        (
            "number",
            "title",
            "review_status",
            "version",
            "tc_in",
            "tc_out",
            "duration",
            "written_to",
        ),
    ),
    (
        "Cue Sheet",
        "sequence_number",
        (
            "sequence_number",
            "status",
            "number",
            "title",
            "usage_theme",
            "usage",
            "tc_in",
            "tc_out",
            "duration",
            "artist",
            "cue_type",
            "plays_as_source",
            "interrupted_use",
            "cue_sheet_notes",
        ),
    ),
]


def _apply(apps: Apps, views: list, renames: dict[str, str]) -> None:
    view = apps.get_model("cues", "View")
    for old, new in renames.items():
        view.objects.filter(project=None, name=old).update(name=new)
    for order, (name, sort, columns) in enumerate(views):
        view.objects.update_or_create(
            project=None,
            name=name,
            defaults={
                "order": order,
                "columns": [{"id": column} for column in columns],
                "sort": [{"id": sort, "dir": "asc"}],
            },
        )


def seed(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    del schema_editor
    _apply(apps, VIEWS, {"Overview": "Standard"})


def unseed(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    del schema_editor
    view = apps.get_model("cues", "View")
    added = ("Orchestration", "Recording", "Mixing", "Viewer")
    view.objects.filter(project=None, name__in=added).delete()
    view.objects.filter(project=None, name="Standard").update(name="Overview")
    view.objects.filter(project=None, name="Cue Sheet").update(order=3)


class Migration(migrations.Migration):
    dependencies = [("cues", "0012_department_fields")]

    operations = [migrations.RunPython(seed, unseed)]
