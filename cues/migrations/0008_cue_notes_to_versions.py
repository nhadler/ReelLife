"""Moves the notes fields onto versions; gives every cue a v1.

Decided Oct 30: IN/OUT notes, spotting notes, temp, notes,
scene description, key, BPM and meter describe a delivery, so they live
on each version, and every cue has at least v1 to hold them. Existing
versions all get the cue's values (the only ones there were); cues
without a version get v1, written to their reel's current turnover so it
doesn't show up as needing conform.
"""

from django.db import migrations

FIELDS = (
    "in_note",
    "out_note",
    "spotting_notes",
    "temp",
    "scene_description",
    "key",
    "bpm",
    "meter",
    "notes",
)


def _current_picture_version(picture_versions, picture_id):
    return (
        picture_versions.filter(picture_id=picture_id)
        .order_by("-turnover__received_date", "-turnover_id")
        .first()
    )


def forwards(apps, schema_editor):
    del schema_editor
    cue_model = apps.get_model("cues", "Cue")
    version_model = apps.get_model("cues", "CueVersion")
    picture_versions = apps.get_model("cues", "PictureVersion").objects
    for cue in cue_model.objects.all():
        values = {name: getattr(cue, name) for name in FIELDS}
        versions = version_model.objects.filter(cue=cue)
        if versions.exists():
            versions.update(**values)
            if cue.current_version_id is None:
                cue.current_version = versions.order_by(
                    "-created_at", "-id"
                ).first()
                cue.save(update_fields=["current_version"])
            continue
        written_to = _current_picture_version(picture_versions, cue.picture_id)
        cue.current_version = version_model.objects.create(
            cue=cue,
            label="v1",
            written_to=written_to,
            tc_in=cue.tc_in,
            tc_out=cue.tc_out,
            seq_start=cue.seq_start,
            **values,
        )
        cue.save(update_fields=["current_version"])


def backwards(apps, schema_editor):
    del schema_editor
    cue_model = apps.get_model("cues", "Cue")
    for cue in cue_model.objects.exclude(current_version=None).select_related(
        "current_version"
    ):
        for name in FIELDS:
            setattr(cue, name, getattr(cue.current_version, name))
        cue.save(update_fields=list(FIELDS))


class Migration(migrations.Migration):
    dependencies = [("cues", "0007_version_notes_fields")]

    operations = [migrations.RunPython(forwards, backwards)]
