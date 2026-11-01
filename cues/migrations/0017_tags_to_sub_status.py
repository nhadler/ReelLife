"""Moves tags to Sub Status (decided Oct 31).

Each project's tags become its Sub Status list (the default order first,
then the rest by name), and each cue's first tag (by name) becomes its
current version's sub status. Data migrations write no history, so each
version changed here gets a snapshot row (see 0011), keeping undo exact.
"""

from django.db import migrations
from django.utils import timezone

# The default sub status order, frozen at the time of writing.
DEFAULT_ORDER = (
    "Awaiting Notes",
    "Conform",
    "Delivered",
    "Respot",
    "Reuse",
    "To Be Presented",
    "Tracked",
)
REASON = "Snapshot after tags became sub status"


def _rank(name):
    return (
        (0, DEFAULT_ORDER.index(name)) if name in DEFAULT_ORDER else (1, name)
    )


def _snapshot(model, historical, obj, now):
    names = {f.name for f in historical._meta.concrete_fields}  # noqa: SLF001
    values = {
        f.attname: getattr(obj, f.attname)
        for f in model._meta.concrete_fields  # noqa: SLF001
        if f.name in names
    }
    historical.objects.create(
        **values,
        history_date=now,
        history_type="~",
        history_change_reason=REASON,
    )


def forwards(apps, schema_editor):
    del schema_editor
    tag_model = apps.get_model("cues", "Tag")
    option_model = apps.get_model("cues", "SubStatusOption")
    cue_model = apps.get_model("cues", "Cue")
    version_model = apps.get_model("cues", "CueVersion")
    historical = apps.get_model("cues", "HistoricalCueVersion")
    now = timezone.now()
    options = {}
    # A set: Tag's default ordering would leak into DISTINCT.
    for project_id in set(
        tag_model.objects.values_list("project_id", flat=True)
    ):
        tags = sorted(
            tag_model.objects.filter(project_id=project_id),
            key=lambda t: _rank(t.name),
        )
        for order, tag in enumerate(tags):
            options[tag.pk] = option_model.objects.create(
                project_id=project_id,
                label=tag.name,
                color=tag.color,
                order=order,
            )
    for cue in cue_model.objects.exclude(current_version=None):
        first = cue.tags.order_by("name").first()
        if first is None:
            continue
        version = version_model.objects.get(pk=cue.current_version_id)
        version.sub_status = options[first.pk]
        version.save(update_fields=["sub_status"])
        _snapshot(version_model, historical, version, now)


def backwards(apps, schema_editor):
    del schema_editor
    apps.get_model("cues", "HistoricalCueVersion").objects.filter(
        history_change_reason=REASON
    ).delete()
    apps.get_model("cues", "CueVersion").objects.update(sub_status=None)
    apps.get_model("cues", "SubStatusOption").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("cues", "0016_sub_status")]

    operations = [migrations.RunPython(forwards, backwards)]
