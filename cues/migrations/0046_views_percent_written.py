"""Re-seeds the eight standard views with their columns.

Adds % Written after Status in Standard, in the seeded view and in each
project's own copy of Standard. Uses a frozen copy of
cues.defaults.global_views() at the time of writing. Reverting takes
the column out of the projects' copies and leaves the seeded views.
"""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

VIEWS = [
    (
        "Standard",
        "tc_in",
        (
            "thumbnail",
            "status",
            "percent_written",
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
            "revision_notes",
            "themes",
            "instrumentation",
            "priority",
            "difficulty",
            "updated_at",
        ),
    ),
    (
        "Spotting",
        "tc_in",
        (
            "thumbnail",
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
            "revision_notes",
            "themes",
            "instrumentation",
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
            "thumbnail",
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
            "thumbnail",
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
            "themes",
            "key",
            "bpm",
            "meter",
            "instrumentation",
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
            "thumbnail",
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
            "instrumentation",
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
            "thumbnail",
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
            "instrumentation",
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
            "thumbnail",
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
            "thumbnail",
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
            "writers",
            "publishers",
            "cue_type",
            "plays_as_source",
            "interrupted_use",
            "cue_sheet_notes",
        ),
    ),
]

COLUMN = "percent_written"


def seed(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    del schema_editor
    view = apps.get_model("cues", "View")
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
    for copy in view.objects.filter(project__isnull=False, name="Standard"):
        ids = [column.get("id") for column in copy.columns]
        if COLUMN in ids or "status" not in ids:
            continue
        copy.columns.insert(ids.index("status") + 1, {"id": COLUMN})
        copy.save(update_fields=["columns"])


def unseed(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    del schema_editor
    view = apps.get_model("cues", "View")
    for copy in view.objects.filter(project__isnull=False, name="Standard"):
        kept = [c for c in copy.columns if c.get("id") != COLUMN]
        if len(kept) != len(copy.columns):
            copy.columns = kept
            copy.save(update_fields=["columns"])


class Migration(migrations.Migration):
    dependencies = [("cues", "0045_project_color")]

    operations = [migrations.RunPython(seed, unseed)]
