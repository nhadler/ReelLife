"""Records a history snapshot of rows whose history is out of date.

Migration 0008 created versions and set cues' current version without
writing history (data migrations don't), so a cue's latest history row
could still say it had no current version. Undoing a later edit restores
that row, and the cue lost its current version. This writes a snapshot
row ("+" for rows with no history, "~" otherwise) for every tracked row
whose latest history doesn't match the database, tags included, so undo
restores what was really there. Cues that already lost their current
version that way get their newest version back first.
"""

from django.db import migrations
from django.utils import timezone

TRACKED = (
    "Project",
    "Turnover",
    "Picture",
    "PictureVersion",
    "Cue",
    "CueVersion",
)
REASON = "Snapshot after data migration"


def _values(model, historical, obj):
    names = {f.name for f in historical._meta.concrete_fields}  # noqa: SLF001
    return {
        f.attname: getattr(obj, f.attname)
        for f in model._meta.concrete_fields  # noqa: SLF001
        if f.name in names
    }


def _tag_ids(tag_history, row):
    if row is None:
        return set()
    return set(
        tag_history.objects.filter(history=row).values_list("tag_id", flat=True)
    )


def _snapshot(apps, name, now):
    model = apps.get_model("cues", name)
    historical = apps.get_model("cues", f"Historical{name}")
    tag_history = (
        apps.get_model("cues", "HistoricalCue_tags") if name == "Cue" else None
    )
    for obj in model.objects.all():
        values = _values(model, historical, obj)
        latest = (
            historical.objects.filter(id=obj.pk).order_by("-history_id").first()
        )
        links = (
            dict(
                model.tags.through.objects.filter(cue_id=obj.pk).values_list(
                    "tag_id", "id"
                )
            )
            if tag_history
            else {}
        )
        tags = set(links)
        same = latest is not None and all(
            getattr(latest, key) == value for key, value in values.items()
        )
        if same and (
            tag_history is None or _tag_ids(tag_history, latest) == tags
        ):
            continue
        row = historical.objects.create(
            **values,
            history_date=now,
            history_type="+" if latest is None else "~",
            history_change_reason=REASON,
        )
        for tag_id in sorted(tags):
            tag_history.objects.create(
                id=links[tag_id], history=row, cue_id=obj.pk, tag_id=tag_id
            )


def _repair_current_versions(apps):
    """Cues that lost their current version to that undo get their newest."""
    versions = apps.get_model("cues", "CueVersion").objects
    for cue in apps.get_model("cues", "Cue").objects.filter(
        current_version=None
    ):
        newest = versions.filter(cue=cue).order_by("-created_at", "-id").first()
        if newest is not None:
            cue.current_version = newest
            cue.save(update_fields=["current_version"])


def forwards(apps, schema_editor):
    del schema_editor
    _repair_current_versions(apps)
    now = timezone.now()
    for name in TRACKED:
        _snapshot(apps, name, now)


def backwards(apps, schema_editor):
    del schema_editor
    for name in TRACKED:
        historical = apps.get_model("cues", f"Historical{name}")
        rows = historical.objects.filter(history_change_reason=REASON)
        if name == "Cue":
            ids = list(rows.values_list("history_id", flat=True))
            apps.get_model("cues", "HistoricalCue_tags").objects.filter(
                history_id__in=ids
            ).delete()
        rows.delete()


class Migration(migrations.Migration):
    dependencies = [("cues", "0010_project_daw_start")]

    operations = [migrations.RunPython(forwards, backwards)]
