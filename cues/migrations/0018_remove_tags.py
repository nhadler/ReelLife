"""Removes tags now that Sub Status has replaced them (see 0017)."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("cues", "0017_tags_to_sub_status"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="cue",
            name="tags",
        ),
        migrations.DeleteModel(
            name="HistoricalCue_tags",
        ),
        # Its constraint goes with it (removing `project` first fails on
        # SQLite: the unique constraint still names the field).
        migrations.RemoveConstraint(model_name="tag", name="tag_name_unique"),
        migrations.DeleteModel(
            name="Tag",
        ),
    ]
