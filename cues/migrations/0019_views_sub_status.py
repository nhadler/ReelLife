"""Re-seeds the eight standard views with their columns.

Sub Status replaces tags. Frozen copy of cues.defaults.global_views() at the time of
writing. Reverting leaves the newer columns in place (harmless).
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
            "sub_status",
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
            "sub_status",
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
            "sub_status",
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
            "sub_status",
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
            "sub_status",
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
            "sub_status",
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
            "review_status",
            "number",
            "title",
            "version",
            "tc_in",
            "tc_out",
            "duration",
            "written_to",
            "current_picture",
            "scene",
            "cue_type",
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


def seed(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    del schema_editor
    view = apps.get_model("cues", "View")
    # Projects' own views: the Tags column becomes Sub Status.
    for own in view.objects.exclude(project=None):
        own.columns = [
            {**c, "id": "sub_status"} if c.get("id") == "tags" else c
            for c in own.columns
        ]
        own.save(update_fields=["columns"])
    for order, (name, sort, columns) in enumerate(VIEWS):
        view.objects.update_or_create(
            project=None,
            name=name,
            defaults={
                "order": order,
                "columns": [{"id": column} for column in columns],
                "sort": [{"id": sort, "dir": "asc"}],
            },
        )


class Migration(migrations.Migration):
    dependencies = [("cues", "0018_remove_tags")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
