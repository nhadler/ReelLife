"""A project's views: the seeded presets plus the project's own.

The eight seeded views are global (no project). Changing one inside a
project saves a copy for that project under the same name, which then
stands in for the global one there; deleting that copy resets the view.
Views a project adds are its own. The seeded views can't be renamed or
deleted.
"""

from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from cues import columns as columns_registry
from cues import defaults
from cues import models

BUILT_IN = frozenset(name for name, _ in defaults.VIEW_PRESETS)
# "none": not pinned, where the grid would pin the column by default.
_PINNED = (None, "left", "right", "none")
_DIRECTIONS = ("asc", "desc")
_WIDTHS = range(30, 2001)


class ViewError(ValueError):
    """The change can't be made; the message is for the user."""


def for_project(project: models.Project) -> list[models.View]:
    """The views a project offers, in order: its copies replace globals."""
    own = {view.name: view for view in project.views.all()}
    built_in = [
        own.pop(view.name, view)
        for view in models.View.objects.filter(project=None)
    ]
    # The seeded views first, in their order; then the project's own.
    added = sorted(own.values(), key=lambda view: (view.order, view.name))
    return [*built_in, *added]


def _columns(value: Any) -> list[dict[str, Any]]:
    """Checks a column layout: [{id, width?, pinned?}, ...]."""
    if not isinstance(value, list) or not value:
        raise ViewError("A view needs at least one column.")
    layout = []
    seen = set()
    for entry in value:
        if (
            not isinstance(entry, dict)
            or entry.get("id") not in columns_registry.BY_ID
            or entry["id"] in seen
        ):
            raise ViewError("The columns aren't ones the grid knows.")
        seen.add(entry["id"])
        width = entry.get("width")
        pinned = entry.get("pinned")
        if width is not None and (
            isinstance(width, bool)
            or not isinstance(width, int)
            or width not in _WIDTHS
        ):
            raise ViewError("A column's width must be 30 to 2000 pixels.")
        if pinned not in _PINNED:
            raise ViewError("A column is pinned left, right or not at all.")
        item: dict[str, Any] = {"id": entry["id"]}
        if width is not None:
            item["width"] = width
        if pinned is not None:
            item["pinned"] = pinned
        layout.append(item)
    return layout


def _sort(value: Any) -> list[dict[str, str]]:
    """Checks a sort: [{id, dir}, ...]."""
    if not isinstance(value, list) or any(
        not isinstance(entry, dict)
        or entry.get("id") not in columns_registry.BY_ID
        or entry.get("dir") not in _DIRECTIONS
        for entry in value
    ):
        raise ViewError("The sort isn't one the grid knows.")
    return [{"id": e["id"], "dir": e["dir"]} for e in value]


def _name(project: models.Project, value: Any, *, keep: str = "") -> str:
    """Checks a new name: 1 to 50 characters, not another view's."""
    name = value.strip() if isinstance(value, str) else ""
    if not name or len(name) > 50:
        raise ViewError("A view needs a name of up to 50 characters.")
    taken = {
        view.name.lower() for view in for_project(project) if view.name != keep
    }
    if name.lower() in taken:
        raise ViewError(f"There's already a view called {name}.")
    return name


def _save(view: models.View) -> models.View:
    try:
        view.full_clean()
    except ValidationError as exc:  # pragma: no cover - checked above
        raise ViewError("; ".join(exc.messages)) from exc
    view.save()
    return view


@transaction.atomic
def create(
    project: models.Project, name: Any, columns: Any, sort: Any = None
) -> models.View:
    """Adds a view to the project, after its others."""
    last = project.views.aggregate(last=Max("order"))["last"]
    top = max(last or 0, len(BUILT_IN))
    return _save(
        models.View(
            project=project,
            name=_name(project, name),
            columns=_columns(columns),
            sort=_sort(sort or []),
            order=top + 1,
        )
    )


@transaction.atomic
def update(
    project: models.Project, view: models.View, changes: dict[str, Any]
) -> models.View:
    """Renames a view or changes its columns or sort.

    A global view is first copied into the project, so other projects
    keep theirs.

    Raises:
        ViewError: An invalid change, or renaming a seeded view.
    """
    if view.project_id not in (None, project.pk):
        raise ViewError("That view belongs to another project.")
    if "name" in changes and changes["name"] != view.name:
        if view.name in BUILT_IN:
            raise ViewError(
                f"{view.name} is a standard view; it keeps its name."
            )
        name = _name(project, changes["name"], keep=view.name)
    else:
        name = view.name
    if view.project_id is None:
        view = models.View(
            project=project,
            name=view.name,
            columns=view.columns,
            sort=view.sort,
            order=view.order,
        )
    view.name = name
    if "columns" in changes:
        view.columns = _columns(changes["columns"])
    if "sort" in changes:
        view.sort = _sort(changes["sort"])
    return _save(view)


def delete(project: models.Project, view: models.View) -> None:
    """Deletes a project's view, or resets a seeded one to its default.

    Raises:
        ViewError: The global view itself, or another project's.
    """
    if view.project_id != project.pk:
        raise ViewError(
            f"{view.name} is a standard view and can't be deleted."
            if view.project_id is None
            else "That view belongs to another project."
        )
    view.delete()


def _free_name(project: models.Project, name: str) -> str:
    """`name`, or `name 2`, `name 3` … if a view already has it."""
    taken = {view.name.lower() for view in for_project(project)}
    candidate, n = name, 1
    while candidate.lower() in taken:
        n += 1
        candidate = f"{name[:46]} {n}"
    return candidate


@transaction.atomic
def duplicate(project: models.Project, view: models.View) -> models.View:
    """A copy of a view, named after it, at the end of the project's."""
    return create(
        project, _free_name(project, view.name), view.columns, view.sort
    )


@transaction.atomic
def move(project: models.Project, view: models.View, by: int) -> None:
    """Moves one of the project's own views up (-1) or down (+1).

    The standard views stay first, in their order.

    Raises:
        ViewError: A standard view or another project's.
    """
    if view.project_id != project.pk or view.name in BUILT_IN:
        raise ViewError("Only your own views can be moved.")
    own = [v for v in for_project(project) if v.name not in BUILT_IN]
    at = own.index(view)
    to = at + by
    if not 0 <= to < len(own):
        return
    own[at], own[to] = own[to], own[at]
    for order, item in enumerate(own, start=len(BUILT_IN) + 1):
        if item.order != order:
            item.order = order
            item.save(update_fields=["order"])


@transaction.atomic
def copy_from(project: models.Project, source: models.Project) -> int:
    """Brings the source's own views into the project.

    Its changed standard views replace this project's; its other views
    are added, renamed when a name is taken.

    Returns:
        How many views were copied.
    """
    copied = 0
    for view in source.views.order_by("order", "name"):
        if view.name in BUILT_IN:
            project.views.filter(name=view.name).delete()
            models.View.objects.create(
                project=project,
                name=view.name,
                columns=view.columns,
                sort=view.sort,
                order=view.order,
            )
        else:
            create(
                project,
                _free_name(project, view.name),
                view.columns,
                view.sort,
            )
        copied += 1
    return copied
