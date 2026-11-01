"""Change set labels for the settings pages' list formsets.

A save of the Statuses, Lists, Departments, Cue Sheet or Credits page is
one change set (cues.services.history); its label says what changed, so
the project's Activity and the Credits page's recent changes read well.
"""

from collections.abc import Iterable
from typing import Any


def describe(action: str, *formsets: Any, also: Iterable[str] = ()) -> str:
    """Says what saving valid list formsets will do, for the change set.

    e.g. "Edit statuses: add Mixing; rename Composed to Written; delete
    Omit". Call it before saving: it reads what each form changed.

    Args:
        action: What the page edits, e.g. "Edit statuses".
        *formsets: The page's validated formsets.
        also: Other changes to mention, e.g. "sync with review status".

    Returns:
        The label, or "" when nothing changed.
    """
    done: dict[str, list[str]] = {
        "add": [],
        "rename": [],
        "change": [],
        "delete": [],
    }
    for formset in formsets:
        for form in formset.forms:
            if not form.has_changed():
                continue
            if form in formset.deleted_forms:
                if form.instance.pk:
                    done["delete"].append(str(form.instance))
            elif not form.instance.pk:
                done["add"].append(str(form.instance))
            elif renamed := [
                name for name in ("label", "name") if name in form.changed_data
            ]:
                old, new = (
                    form.initial[renamed[0]],
                    form.cleaned_data[renamed[0]],
                )
                done["rename"].append(f"{old} to {new}")
            else:
                done["change"].append(str(form.instance))
    parts = [*also] + [
        f"{verb} {', '.join(names)}" for verb, names in done.items() if names
    ]
    return f"{action}: {'; '.join(parts)}" if parts else ""
