"""Change sets and undo.

An import or tool runs inside `recording(...)`. Every history row
django-simple-history writes meanwhile is tagged with the change set and
an order number, so `undo` can walk it backwards: delete what it created,
restore what it changed, recreate what it deleted. Undo is itself a change
set, so it shows in history like any other change.

Undo refuses if something in the change set was edited afterwards, rather
than silently overwriting that later edit.
"""

from collections.abc import Callable, Iterator
import contextlib
import contextvars
import dataclasses
import itertools
from typing import Any

from django.db import IntegrityError
from django.db import connections
from django.db import models as db_models
from django.db import transaction
from django.db.models import Max
from django.db.models import OuterRef
from django.db.models import ProtectedError
from django.db.models import Subquery
from django.utils import timezone
from django.utils.text import capfirst

from accounts.models import User
from cues import models

# Models whose history can be undone, in no particular order.
TRACKED: tuple[type[db_models.Model], ...] = (
    models.Project,
    models.Turnover,
    models.Picture,
    models.PictureVersion,
    models.Cue,
    models.CueVersion,
    models.VersionTheme,
    models.CueInstrument,
    models.ProjectNote,
    models.SchedulePin,
    models.WritingLog,
    models.CueShare,
    models.Party,
    models.DefaultShare,
    models.SeriesShare,
    models.StatusOption,
    models.SubStatusOption,
    models.ThemeOption,
    models.InstrumentOption,
    models.DepartmentStatus,
)


def _department_uses(option: models.DepartmentStatus) -> int:
    return option.project.cues.filter(**{option.field: option.name}).count()


def _party_uses(party: models.Party) -> int:
    return sum(
        model.objects.filter(party=party).count()
        for model in (models.CueShare, models.DefaultShare, models.SeriesShare)
    )


# List entries that cues, versions or shares point at: how many use one
# now, and what they are. Undo won't take away an entry still in use.
_USES: dict[type[db_models.Model], tuple[Callable[[Any], int], str]] = {
    models.StatusOption: (lambda o: o.cues.count(), "cue"),
    models.SubStatusOption: (
        lambda o: models.CueVersion.objects.filter(sub_status=o).count(),
        "version",
    ),
    models.ThemeOption: (
        lambda o: models.VersionTheme.objects.filter(theme=o).count(),
        "version",
    ),
    models.InstrumentOption: (
        lambda o: models.CueInstrument.objects.filter(instrument=o).count(),
        "cue",
    ),
    models.DepartmentStatus: (_department_uses, "cue"),
    models.Party: (_party_uses, "credit"),
}

# References a restored row isn't checked for: its project is there if
# we are undoing in it, and a cue's current version comes back with it.
_UNCHECKED = frozenset({"project", "current_version"})


@dataclasses.dataclass
class _Recording:
    change_set: models.ChangeSet
    sequence: Callable[[], int]


_CURRENT: contextvars.ContextVar[_Recording | None] = contextvars.ContextVar(
    "reeltime_change_set", default=None
)


class UndoError(Exception):
    """A change set can't be undone; the message says why."""


class ChangedSinceError(UndoError):
    """Something the change set touched was changed afterwards."""


_LABEL_LENGTH = models.ChangeSet._meta.get_field("label").max_length or 200

# Where changes come from when not the web app, e.g. "MCP, claude.ai".
_VIA: contextvars.ContextVar[str] = contextvars.ContextVar(
    "history_via", default=""
)


@contextlib.contextmanager
def via(source: str) -> Iterator[None]:
    """Labels the change sets recorded inside as made through `source`.

    The MCP server wraps every tool in this, so History shows e.g.
    "Edit 1m01 Title (via MCP, claude.ai)".
    """
    token = _VIA.set(source)
    try:
        yield
    finally:
        _VIA.reset(token)


@contextlib.contextmanager
def recording(
    project: models.Project | None, label: str, user: User | None = None
) -> Iterator[models.ChangeSet]:
    """Records every tracked change inside the block as one change set.

    Runs in a transaction: if the block raises, nothing is kept.

    Args:
        project: The project the changes belong to, or None for what
            every project shares (the Credits list).
        label: What the user did, e.g. 'Import cue_list.csv'.
        user: Who did it.

    Yields:
        The change set.
    """
    source = _VIA.get()
    if source:
        label = f"{label} (via {source})"
    with transaction.atomic():
        change_set = models.ChangeSet.objects.create(
            project=project, label=label[:_LABEL_LENGTH], user=user
        )
        counter = itertools.count(1)
        token = _CURRENT.set(_Recording(change_set, lambda: next(counter)))
        try:
            yield change_set
        finally:
            _CURRENT.reset(token)


def tag_history_row(history_instance: Any, **kwargs: Any) -> None:
    """pre_create_historical_record receiver: links rows to the change set."""
    del kwargs
    current = _CURRENT.get()
    if current is not None:
        history_instance.change_set = current.change_set
        history_instance.change_seq = current.sequence()


def _rows(change_set: models.ChangeSet) -> list[Any]:
    rows: list[Any] = []
    for model in TRACKED:
        # simple_history adds the manager when the model class is built.
        manager = getattr(model, "history")  # noqa: B009
        rows.extend(manager.filter(change_set=change_set))  # pylint: disable=no-member
    rows.sort(key=lambda row: row.change_seq or 0, reverse=True)
    return rows


def cancelled_out(change_set: models.ChangeSet | None) -> bool:
    """Whether a later change set leaves no net change.

    An undone change set and the undo that undid it cancel out, so they
    don't block undoing something earlier: that's what lets Ctrl+Z step
    back through several edits. Undoing an undo (from a cue's History)
    re-applies the original change, so it counts as a change again; an
    undo of that cancels once more, and so on.
    """
    if change_set is None:
        return False  # untracked edits (e.g. the admin) always block
    if change_set.undone_at is not None:
        return True
    depth = 0
    undone: models.ChangeSet | None = change_set.undo_of
    while undone is not None:
        depth += 1
        undone = undone.undo_of
    return depth % 2 == 1


def _cancelled(
    change_set: models.ChangeSet | None, memo: dict[int, bool]
) -> bool:
    """cancelled_out, remembered: many rows share a few change sets."""
    if change_set is None:
        return False
    if change_set.pk not in memo:
        memo[change_set.pk] = cancelled_out(change_set)
    return memo[change_set.pk]


def _check_nothing_changed_since(rows: list[Any]) -> None:
    """Refuses if a row the change set touched has changed since.

    One query per model for every object's later history, rather than
    one per object.
    """
    last_row: dict[tuple[type[db_models.Model], int], Any] = {}
    for row in rows:
        key = (row.instance_type, row.id)
        if key not in last_row or row.history_id > last_row[key].history_id:
            last_row[key] = row
    by_model: dict[type[db_models.Model], dict[int, int]] = {}
    for (model, pk), row in last_row.items():
        by_model.setdefault(model, {})[pk] = row.history_id
    memo: dict[int, bool] = {}
    blocking: dict[tuple[type[db_models.Model], int], Any] = {}
    for model, last in by_model.items():
        later = (
            model.history.filter(  # type: ignore[attr-defined]
                id__in=list(last), history_id__gt=min(last.values())
            )
            .select_related("change_set__undo_of")
            .order_by("history_id")
        )
        for newer in later:
            key = (model, newer.id)
            if (
                key not in blocking
                and newer.history_id > last[newer.id]
                and not _cancelled(newer.change_set, memo)
            ):
                blocking[key] = newer
    for key in last_row:  # newest change first, as before
        if key in blocking:
            raise ChangedSinceError(
                f"{key[0]._meta.verbose_name} "  # noqa: SLF001
                f"'{blocking[key].instance}' was changed afterwards; undo "
                "that first."
            )


def _previous_rows(rows: list[Any]) -> dict[tuple[Any, int], Any]:
    """Each changed row's previous snapshot, by (model, history_id).

    Two queries per model: the previous history_id of every row through
    a subquery, then those rows.
    """
    changed: dict[Any, list[int]] = {}
    for row in rows:
        if row.history_type == "~":
            changed.setdefault(row.instance_type, []).append(row.history_id)
    found: dict[tuple[Any, int], Any] = {}
    for model, ids in changed.items():
        manager = model.history
        earlier = (
            manager.filter(
                id=OuterRef("id"), history_id__lt=OuterRef("history_id")
            )
            .order_by("-history_id")
            .values("history_id")[:1]
        )
        pairs = list(
            manager.filter(history_id__in=ids)
            .annotate(previous=Subquery(earlier))
            .values_list("history_id", "previous")
        )
        snapshots = manager.in_bulk([p for _, p in pairs if p is not None])
        for history_id, previous in pairs:
            found[(model, history_id)] = snapshots.get(previous)
    return found


def _what(model: type[db_models.Model]) -> str:
    return str(model._meta.verbose_name)  # noqa: SLF001


def missing_reference(record: Any, current: Any = None) -> str:
    """What a snapshot refers to that no longer exists, if anything.

    Args:
        record: The history row to save back.
        current: The history row of the state it replaces, if the row
            exists: references the two share aren't checked again.

    Returns:
        The missing object's kind, e.g. "status", or "" when all is there.
    """
    for field in record.instance_type._meta.concrete_fields:  # noqa: SLF001
        if not field.is_relation or field.name in _UNCHECKED:
            continue
        value = getattr(record, field.attname)
        if value is None or (
            current is not None and getattr(current, field.attname) == value
        ):
            continue
        target = field.related_model
        if not target.objects.filter(pk=value).exists():
            return _what(target)
    return ""


def restore(record: Any, current: Any = None) -> None:
    """Saves a history snapshot back as the row's current state.

    Args:
        record: The snapshot.
        current: The history row of the state it replaces, if the row
            exists.

    Raises:
        UndoError: It refers to something deleted since, or it clashes
            with a row added since (a name or cue number now taken).
    """
    model = record.instance_type
    missing = missing_reference(record, current)
    if missing:
        raise UndoError(
            f"A {_what(model)} can't be put back: the {missing} it uses was "
            "deleted since. Undo that deletion first."
        )
    try:
        # A savepoint, so the transaction can still name the row below.
        with transaction.atomic():
            _restore(record)
    except IntegrityError as exc:
        raise UndoError(
            f"{capfirst(_what(model))} '{record.instance}' can't be put back: "
            "another one has its name or number now."
        ) from exc


def _restore(record: Any) -> None:
    """Saves a snapshot back, keeping when the row was first created.

    Re-inserting a deleted row would otherwise stamp its auto_now_add
    fields with now, which reorders versions and changes which one
    counts as the latest.
    """
    instance = record.instance
    instance.save()
    kept = {
        field.attname: getattr(record, field.attname)
        for field in instance._meta.concrete_fields  # noqa: SLF001
        if getattr(field, "auto_now_add", False)
    }
    if all(getattr(instance, name) == value for name, value in kept.items()):
        return
    model = type(instance)
    model.objects.filter(pk=instance.pk).update(**kept)
    latest = (
        model.history.filter(id=instance.pk)
        .order_by("-history_id")
        .values_list("history_id", flat=True)
        .first()
    )
    model.history.filter(history_id=latest).update(**kept)


def _refuse_if_in_use(model: Any, pk: int) -> None:
    """Refuses to remove a list entry that something uses now."""
    uses = _USES.get(model)
    if uses is None:
        return
    count_uses, noun = uses
    for entry in model.objects.filter(pk=pk):  # none if already gone
        count = count_uses(entry)
        if count:
            nouns = noun if count == 1 else f"{noun}s"
            raise UndoError(
                f"{capfirst(_what(model))} '{entry}' is used by {count} "
                f"{nouns} now, so it can't be taken away. Change those first."
            )


def _refuse_renaming_in_use(row: Any, previous: Any) -> None:
    """Refuses to rename back a department status cues now use.

    Cues hold the label itself, so they'd be left with one the list no
    longer has. Cues the change set itself renamed are back by now:
    they were saved after the list.
    """
    if row.instance_type is not models.DepartmentStatus:
        return
    if previous.name == row.name:
        return
    count = _department_uses(row.instance)
    if count:
        cues = "cue uses" if count == 1 else "cues use"
        raise UndoError(
            f"{count} {cues} '{row.name}' now, so it can't go back to "
            f"'{previous.name}'. Change those first."
        )


def _delete_created(rows: list[Any]) -> None:
    """Deletes what the change set created: one delete per model.

    Runs after every other row is back to its state before the change
    set, when nothing can still point at these (they didn't exist then).
    Models go newest first, so children before parents.
    """
    by_model: dict[Any, list[int]] = {}
    for row in rows:  # newest first
        by_model.setdefault(row.instance_type, []).append(row.id)
    for model, pks in by_model.items():
        for pk in pks:
            _refuse_if_in_use(model, pk)
        model.objects.filter(pk__in=pks).delete()


def _revert(row: Any, previous: Any) -> None:
    """Puts a changed row back to its previous snapshot."""
    # A change can't follow a deletion of the same row: the id was
    # reused (see keep_ids_unique), so the older rows are another
    # object's. Refuse rather than restore the wrong thing.
    if previous is None or previous.history_type == "-":
        raise UndoError(
            f"The history of {row.instance_type._meta.verbose_name} "  # noqa: SLF001
            f"'{row.instance}' is incomplete, so this can't be undone."
        )
    _refuse_renaming_in_use(row, previous)
    restore(previous, current=row)


def keep_ids_unique(using: str = "default", **kwargs: Any) -> None:
    """post_migrate receiver: never hand out an id that has history.

    History rows refer to objects by id. When a migration rebuilds a
    SQLite table, its AUTOINCREMENT counter restarts at the largest id
    still present, so the id of a deleted row (whose history remains) can
    be given to a new row and undo would mix the two up. This moves each
    tracked table's counter past every id in its history.
    """
    del kwargs
    connection = connections[using]
    if connection.vendor != "sqlite":
        return  # pragma: no cover - ReelTime runs on SQLite
    existing = set(connection.introspection.table_names())
    with connection.cursor() as cursor:
        for model in TRACKED:
            table = model._meta.db_table  # noqa: SLF001
            manager = getattr(model, "history")  # noqa: B009
            history_table = manager.model._meta.db_table  # noqa: SLF001 pylint: disable=no-member
            if history_table not in existing:
                continue  # migrated back past the table (e.g. to zero)
            highest = manager.using(using).aggregate(  # pylint: disable=no-member
                highest=Max("id")
            )["highest"]
            if highest is None:
                continue
            cursor.execute(
                "UPDATE sqlite_sequence SET seq = max(seq, %s) WHERE name = %s",
                [highest, table],
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    "INSERT INTO sqlite_sequence (name, seq) VALUES (%s, %s)",
                    [table, highest],
                )


def undo(
    change_set: models.ChangeSet, user: User | None = None
) -> models.ChangeSet:
    """Reverts everything a change set did, as a new change set.

    Args:
        change_set: What to undo.
        user: Who is undoing it.

    Returns:
        The change set recording the undo.

    Raises:
        UndoError: Already undone, changed since, or the database refuses
            (e.g. a picture created by an import now holds other cues).
    """
    try:
        with recording(
            change_set.project, f"Undo: {change_set.label}", user
        ) as undo_set:
            # Checked inside the transaction, which holds SQLite's writer
            # lock (IMMEDIATE), so nothing can change in between.
            change_set.refresh_from_db(fields=["undone_at"])
            if change_set.undone_at is not None:
                raise UndoError(f"'{change_set.label}' was already undone.")
            rows = _rows(change_set)
            _check_nothing_changed_since(rows)
            previous = _previous_rows(rows)
            for row in rows:
                if row.history_type == "~":
                    _revert(row, previous[(row.instance_type, row.history_id)])
                elif row.history_type == "-":  # deleted inside the change set
                    restore(row)
            _delete_created([row for row in rows if row.history_type == "+"])
            undo_set.undo_of = change_set
            undo_set.save(update_fields=["undo_of"])
            change_set.undone_at = timezone.now()
            change_set.save(update_fields=["undone_at"])
    except (ProtectedError, IntegrityError) as exc:
        raise UndoError(
            f"'{change_set.label}' can't be undone: later changes depend on it."
        ) from exc
    return undo_set
