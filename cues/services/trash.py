"""A project's Trash: deleted cues, and bringing one back.

A deleted cue's history ends with its deletion, and the cue no longer
exists. Restoring it puts back the cue with its versions, shares,
themes, instrumentation and review comments as the deleting action left
them, as one new change set ("Restore 1m05"), so it works even when
later changes make undoing that whole action impossible. Deleted
turnovers and review links aren't covered.
"""

# pylint: disable=no-member  # simple_history's managers

from collections.abc import Iterable
import dataclasses
import datetime
from typing import Any

from django.db.models import Max
from django.db.models import QuerySet

from accounts.models import User
from cues import models
from cues import numbering
from cues.services import activity
from cues.services import history


class TrashError(history.UndoError):
    """The cue can't be restored; the message says why."""


@dataclasses.dataclass(frozen=True)
class Deleted:
    """A cue in the Trash.

    Attributes:
        cue_id: The deleted cue's id.
        number: Its cue number.
        title: Its title.
        when: When it was deleted.
        by: What deleted it (the change set's label).
        user: Who ("" when unknown).
        taken: Whether another active cue has its number now.
        suggestion: The number it can come back as instead, if taken.
    """

    cue_id: int
    number: str
    title: str
    when: datetime.datetime
    by: str
    user: str
    taken: bool = False
    suggestion: str = ""


def _history(model: Any) -> Any:
    return getattr(model, "history")  # noqa: B009


def deletions(project: models.Project) -> QuerySet[Any]:
    """The last history row of each cue of the project that was deleted.

    Newest first.
    """
    rows = _history(models.Cue)
    last = (
        rows.filter(project_id=project.pk)
        .values("id")
        .annotate(last=Max("history_id"))
        .values("last")
    )
    deleted: QuerySet[Any] = (
        rows.filter(history_id__in=last, history_type="-")
        .exclude(id__in=models.Cue.objects.values("id"))
        .select_related("change_set__user", "history_user")
        .order_by("-history_date", "-history_id")
    )
    return deleted


def _taken(project: models.Project) -> set[str]:
    return {
        number.casefold()
        for number in project.cues.filter(active=True).values_list(
            "number", flat=True
        )
    }


def _suggest(number: str, taken: set[str]) -> str:
    """The number with an "r" (then r2, r3 …) that no active cue has."""
    suffix, count = "r", 1
    while f"{number}{suffix}".casefold() in taken:
        count += 1
        suffix = f"r{count}"
    return f"{number}{suffix}"


def _restore_as(project: models.Project, number: str, taken: set[str]) -> str:
    """_suggest's number, or '' if it doesn't fit Valid Cue Numbers."""
    suggestion = _suggest(number, taken)
    if numbering.misfit(suggestion, project.cue_number_pattern):
        return ""
    return suggestion


def entries(project: models.Project, rows: Iterable[Any]) -> list[Deleted]:
    """The Trash's rows for these deletion history rows."""
    taken = _taken(project)
    result = []
    for row in rows:
        is_taken = row.active and row.number.casefold() in taken
        change_set = row.change_set
        result.append(
            Deleted(
                cue_id=row.id,
                number=row.number,
                title=row.title,
                when=row.history_date,
                by=change_set.label if change_set else "Deleted",
                user=activity.user_name(
                    change_set.user if change_set else row.history_user
                ),
                taken=is_taken,
                suggestion=(
                    _restore_as(project, row.number, taken) if is_taken else ""
                ),
            )
        )
    return result


def _same_action(rows: Any, deletion: Any) -> Any:
    """Rows deleted by the action that deleted the cue."""
    rows = rows.filter(history_type="-")
    if deletion.change_set_id is not None:
        return rows.filter(change_set_id=deletion.change_set_id)
    # Deleted outside a change set (the admin): moments before the cue.
    window = deletion.history_date - datetime.timedelta(seconds=5)
    return rows.filter(
        change_set=None,
        history_date__gte=window,
        history_date__lte=deletion.history_date,
    )


def _current_version(deletion: Any, versions: list[Any]) -> int | None:
    """The version that was current before the cue was deleted.

    Deleting clears it first, so the deletion row can't say; the row
    before the deleting action can. Else the newest version.
    """
    ids = {row.id for row in versions}
    if deletion.current_version_id in ids:
        return int(deletion.current_version_id)
    earlier = _history(models.Cue).filter(
        id=deletion.id, history_id__lt=deletion.history_id
    )
    if deletion.change_set_id is not None:
        earlier = earlier.exclude(change_set_id=deletion.change_set_id)
    before = earlier.order_by("-history_id").first()
    if before is not None and before.current_version_id in ids:
        return int(before.current_version_id)
    newest = max(versions, key=lambda r: (r.created_at, r.id), default=None)
    return newest.id if newest is not None else None


def _put_back(number: str, record: Any) -> None:
    missing = history.missing_reference(record)
    if missing:
        raise TrashError(
            f"{number} can't be restored: a {missing} it uses was deleted "
            "since. Put that back first; Activity shows what deleted it."
        )
    history.restore(record)


def restore(
    project: models.Project,
    cue_id: int,
    user: User | None = None,
    *,
    renumber: bool = False,
) -> models.Cue:
    """Brings a deleted cue back, as one change set.

    Args:
        project: The cue's project.
        cue_id: The deleted cue's id.
        user: Who is restoring it.
        renumber: Give it a free number with an "r" when another active
            cue has its own now.

    Returns:
        The restored cue.

    Raises:
        TrashError: It isn't in the Trash, its number is taken (and
            `renumber` is off), or something it uses was deleted since.
        history.UndoError: The database refused (history.restore).
    """
    deletion = deletions(project).filter(id=cue_id).first()
    if deletion is None:
        raise TrashError("That cue isn't in the Trash any more.")
    number = deletion.number
    taken = _taken(project)
    if deletion.active and number.casefold() in taken:
        suggestion = _restore_as(project, number, taken)
        if not suggestion:
            raise TrashError(
                f"Another active cue is {number} now, and "
                f"{_suggest(number, taken)} doesn't fit Valid Cue Numbers; "
                f"renumber that {number} first."
            )
        if not renumber:
            raise TrashError(
                f"Another active cue is {number} now; restore this one as "
                f"{suggestion} instead."
            )
        deletion.number = suggestion
    versions = list(
        _same_action(
            _history(models.CueVersion).filter(cue_id=cue_id), deletion
        )
    )
    deletion.current_version_id = _current_version(deletion, versions)
    with history.recording(project, f"Restore {deletion.number}", user):
        _put_back(number, deletion)
        for row in versions:
            _put_back(number, row)
        version_ids = [row.id for row in versions]
        linked = [
            _history(models.VersionTheme).filter(version_id__in=version_ids),
            _history(models.ReviewComment).filter(version_id__in=version_ids),
            _history(models.CueInstrument).filter(cue_id=cue_id),
            _history(models.CueShare).filter(cue_id=cue_id),
        ]
        for rows in linked:
            for row in _same_action(rows, deletion).order_by("history_id"):
                _put_back(number, row)
    return models.Cue.objects.get(pk=cue_id)
