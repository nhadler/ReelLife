"""Seeds the four global grid views (5)."""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

# Frozen copy of cues.defaults.global_views() at the time of writing, so
# later edits to the defaults don't change what this migration did.
_COMMON = ("status", "number", "title", "version", "tags")
VIEWS = [
    {
        "name": "Overview",
        "order": 0,
        "columns": [
            *_COMMON,
            "tc_in",
            "tc_out",
            "duration",
            "written_to",
            "current_picture",
            "notes",
            "priority",
            "updated_at",
        ],
        "sort": "tc_in",
    },
    {
        "name": "Spotting",
        "order": 1,
        "columns": [
            *_COMMON,
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
        ],
        "sort": "tc_in",
    },
    {
        "name": "Conform",
        "order": 2,
        "columns": [
            *_COMMON,
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
        ],
        "sort": "tc_in",
    },
    {
        "name": "Cue Sheet",
        "order": 3,
        "columns": [
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
        ],
        "sort": "sequence_number",
    },
]


def seed(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    del schema_editor
    view = apps.get_model("cues", "View")
    for spec in VIEWS:
        view.objects.update_or_create(
            project=None,
            name=spec["name"],
            defaults={
                "order": spec["order"],
                "columns": [{"id": column} for column in spec["columns"]],
                "sort": [{"id": spec["sort"], "dir": "asc"}],
                "filter": {},
            },
        )


def unseed(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    del schema_editor
    view = apps.get_model("cues", "View")
    names = [spec["name"] for spec in VIEWS]
    view.objects.filter(project=None, name__in=names).delete()


class Migration(migrations.Migration):
    dependencies = [("cues", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed)]
