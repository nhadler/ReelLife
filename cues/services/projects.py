"""Creating projects with their default statuses and sub statuses."""

from typing import TYPE_CHECKING, Any

from django.db import transaction
from simple_history import utils

from cues import defaults
from cues import models

if TYPE_CHECKING:
    from django.core.files.base import ContentFile


def seed_project(project: models.Project) -> None:
    """Gives a project the default status, sub status and department lists.

    Does nothing for lists the project already has, so it is safe to
    call again.

    Args:
        project: A saved project.
    """
    # With history, so the first edit of an entry can be undone.
    if not project.statuses.exists():
        utils.bulk_create_with_history(
            [
                models.StatusOption(
                    project=project,
                    label=label,
                    kind=kind,
                    color=defaults.TAILWIND[color],
                    order=order,
                )
                for order, (label, kind, color) in enumerate(defaults.STATUSES)
            ],
            models.StatusOption,
        )
    if not project.department_statuses.exists():
        utils.bulk_create_with_history(
            [
                models.DepartmentStatus(
                    project=project,
                    field=field,
                    name=label,
                    color=defaults.TAILWIND[color],
                    order=order,
                )
                for field, statuses in models.DEPARTMENT_STATUSES.items()
                for order, (label, _, color) in enumerate(statuses)
            ],
            models.DepartmentStatus,
        )
    if not project.sub_statuses.exists():
        utils.bulk_create_with_history(
            [
                models.SubStatusOption(
                    project=project,
                    label=label,
                    color=defaults.TAILWIND[color],
                    order=order,
                )
                for order, (label, color) in enumerate(defaults.SUB_STATUSES)
            ],
            models.SubStatusOption,
        )


@transaction.atomic
def create_project(**fields: Any) -> models.Project:
    """Creates a validated project with default statuses and sub statuses.

    Args:
        **fields: Project field values.

    Returns:
        The saved project.

    Raises:
        django.core.exceptions.ValidationError: If the fields are invalid.
    """
    project = models.Project(**fields)
    project.full_clean()
    project.save()
    seed_project(project)
    return project


PLACEHOLDER_PICTURE = models.PLACEHOLDER_PICTURE


def placeholder_picture(project: models.Project) -> models.Picture:
    """Returns the project's "N/A" picture, creating it if needed.

    Cues must belong to a picture; this holds the ones whose picture isn't
    known yet, e.g. imported before any reel exists.
    """
    picture, _ = models.Picture.objects.get_or_create(
        project=project, name=PLACEHOLDER_PICTURE, defaults={"order": 0}
    )
    return picture


def default_status(project: models.Project) -> models.StatusOption | None:
    """Returns the status new cues start with ('To Write' when present)."""
    return (
        project.statuses.filter(label=defaults.DEFAULT_STATUS).first()
        or project.statuses.first()
    )


def set_thumbnail(
    project: models.Project, content: "ContentFile[bytes] | None"
) -> None:
    """Replaces (or with None, removes) a project's thumbnail.

    The old file is deleted once the new one is saved.

    Args:
        project: The project.
        content: A processed image from thumbnails.save_upload, or None.
    """
    old = project.thumbnail.name
    if content is None:
        project.thumbnail = None
        project.save(update_fields=["thumbnail", "updated_at"])
    else:
        project.thumbnail.save(content.name or "thumbnail.webp", content)
    if old and old != project.thumbnail.name:
        project.thumbnail.storage.delete(old)
