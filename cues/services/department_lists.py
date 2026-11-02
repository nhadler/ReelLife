"""Saving a project's department status lists.

A cue keeps the label of its department status, so renaming an entry
renames it on the project's cues, and an entry that cues use can't be
deleted. The list and the cues change in one change set, so Undo puts
both back.
"""

from typing import Any

from accounts.models import User
from cues import models
from cues.services import history


def in_use(project: models.Project, formset: Any) -> bool:
    """Marks entries set for deletion that cues still use.

    Returns:
        Whether any were found (their forms carry the error).
    """
    blocked = False
    for form in formset.deleted_forms:
        option = form.instance
        if not option.pk:
            continue
        count = project.cues.filter(**{option.field: option.name}).count()
        if count:
            cues = "cue" if count == 1 else "cues"
            form.add_error(
                None,
                f"{option.name} is used by {count} {cues}; give them another "
                "status first.",
            )
            blocked = True
    return blocked


def save(
    project: models.Project,
    formsets: dict[str, Any],
    label: str,
    user: User | None = None,
) -> None:
    """Saves each department's formset, renaming labels on cues.

    Args:
        project: The project.
        formsets: Valid formsets by department field.
        label: The change set's label (change_labels.describe); "" when
            nothing changed, so there is nothing to save.
        user: Who saved.
    """
    if not label:
        return
    renames = [
        (field, form.initial["name"], form.cleaned_data["name"])
        for field, formset in formsets.items()
        for form in formset.forms
        if form.instance.pk
        and form not in formset.deleted_forms
        and "name" in form.changed_data
    ]
    with history.recording(project, label, user):
        # The list first: undo puts the cues back before renaming it.
        for field, formset in formsets.items():
            for option in formset.save(commit=False):
                option.field = field
                option.save()
            for option in formset.deleted_objects:
                option.delete()
        for field, old, new in renames:
            for cue in project.cues.filter(**{field: old}):
                setattr(cue, field, new)
                cue.save()
