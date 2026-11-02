"""Project-wide settings work: copy lists and views, duplicate, delete.

Used by the Views and Project settings pages. Copying adds missing
entries without overwriting existing ones. Duplicating creates a
project with the same setup and no cues. Deleting removes the project
and its contents.
"""

from collections.abc import Iterable

from django.db import transaction

from cues import models
from cues.services import projects as project_service
from cues.services import thumbnails
from cues.services import view_presets

# Project fields a duplicate keeps (not title, numbers, thumbnail or
# the archive flag).
_SETUP_FIELDS = (
    "kind",
    "series",
    "season",
    "frame_rate",
    "drop_frame",
    "picture_type",
    "lanes",
    "production_company",
    "distributor",
    "country",
    "cue_number_pattern",
    "sync_review_status",
    "cue_thumbnail_fallback",
)


def _missing(existing: Iterable[str], label: str) -> bool:
    return label.lower() not in {name.lower() for name in existing}


def copy_lists(project: models.Project, source: models.Project) -> int:
    """Adds the source's statuses and lists that the project lacks.

    Statuses, sub statuses, themes, instruments and the department
    lists, matched by name ignoring case, each added after the project's
    own.

    Returns:
        How many entries were added.
    """
    added = 0
    with transaction.atomic():
        labels = list(project.statuses.values_list("label", flat=True))
        base = len(labels)
        for status in source.statuses.all():
            if _missing(labels, status.label):
                models.StatusOption.objects.create(
                    project=project,
                    label=status.label,
                    kind=status.kind,
                    color=status.color,
                    order=base + status.order,
                )
                labels.append(status.label)
                added += 1
        labels = list(project.sub_statuses.values_list("label", flat=True))
        for sub in source.sub_statuses.all():
            if _missing(labels, sub.label):
                models.SubStatusOption.objects.create(
                    project=project,
                    label=sub.label,
                    color=sub.color,
                    order=len(labels),
                )
                labels.append(sub.label)
                added += 1
        for related in ("themes", "instruments", "department_statuses"):
            added += _copy_named(project, source, related)
    return added


def _copy_named(
    project: models.Project, source: models.Project, related: str
) -> int:
    """Copies entries of a name-and-colour list (by `related` name)."""
    added = 0
    mine = getattr(project, related)
    for option in getattr(source, related).all():
        field = getattr(option, "field", None)
        same_list = mine.filter(field=field) if field else mine.all()
        if _missing(same_list.values_list("name", flat=True), option.name):
            type(option).objects.create(
                project=project,
                name=option.name,
                color=option.color,
                order=same_list.count(),
                **({"field": field} if field else {}),
            )
            added += 1
    return added


def duplicate(project: models.Project, title: str) -> models.Project:
    """A new project with this one's setup, lists, views and deadlines.

    No cues, turnovers, pictures or notes come along.

    Raises:
        django.core.exceptions.ValidationError: An invalid title.
    """
    with transaction.atomic():
        copy = project_service.create_project(
            title=title,
            **{name: getattr(project, name) for name in _SETUP_FIELDS},
        )
        # create_project seeded the default lists; make them this
        # project's instead.
        copy.statuses.all().delete()
        copy.sub_statuses.all().delete()
        copy.department_statuses.all().delete()
        copy_lists(copy, project)
        view_presets.copy_from(copy, project)
        for deadline in project.deadlines.all():
            models.Deadline.objects.create(
                project=copy,
                kind=deadline.kind,
                label=deadline.label,
                start_date=deadline.start_date,
                end_date=deadline.end_date,
            )
    return copy


def set_archived(project: models.Project, archived: bool) -> None:
    """Archives a project (the dashboard hides it) or brings it back."""
    project.archived = archived
    project.save(update_fields=["archived", "updated_at"])


def delete(project: models.Project) -> None:
    """Deletes a project and everything in it, cue thumbnails included.

    This can't be undone: its history goes with it.
    """
    # Cue thumbnails outlive their cues (undo points back at them); the
    # project's own goes with it (cues.apps).
    files = list(
        project.cues.exclude(thumbnail="").values_list("thumbnail", flat=True)
    )
    with transaction.atomic():
        cues = models.Cue.objects.filter(project=project)
        cues.update(current_version=None)
        models.CueVersion.objects.filter(cue__project=project).delete()
        cues.delete()  # statuses and pictures protect against cues
        project.delete()
    for name in files:
        thumbnails.storage().delete(name)
