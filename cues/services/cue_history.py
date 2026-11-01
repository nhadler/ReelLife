"""A cue's change history, as the details page shows it.

Every history row for the cue and its versions, grouped by the change set
that wrote it (one user action), newest first, with each changed field
shown before and after in the project's terms: timecodes, status labels,
turnover labels. Reverting an entry undoes its change set
(`history.undo`), which refuses if something was changed afterwards.
"""

from collections import defaultdict
from collections.abc import Iterable
import dataclasses
import datetime
from typing import Any, cast

from django.db import models as db_models
from django.utils import timezone
from django.utils.text import capfirst

from cues import models
from cues import timecode
from cues.services import history

_SKIP = frozenset({"id", "project", "cue", "created_at", "updated_at", "lane"})
_TIMECODES = frozenset({"tc_in", "tc_out", "seq_start"})
_LABELS = {"tc_in": "IN", "tc_out": "OUT", "seq_start": "Seq. Start"}


@dataclasses.dataclass(frozen=True)
class Change:
    """One line of an entry.

    Attributes:
        subject: "Cue" or the version label, e.g. "v2".
        kind: "created", "deleted" or "changed".
        field: The field's label ("changed" only).
        old: The value before, for display.
        new: The value after, for display.
    """

    subject: str
    kind: str
    field: str = ""
    old: str = ""
    new: str = ""


@dataclasses.dataclass
class Entry:
    """One user action that touched the cue.

    Attributes:
        change_set: The change set, or None for changes made outside
            ReelLife's screens (e.g. the admin or the demo seed).
        label: What was done.
        user: Who did it ("" when unknown).
        when: When.
        changes: What changed.
        other_cues: How many other cues the same action touched (an
            import, say); reverting reverts those too.
        blocked: A later change to this cue must be reverted first.
    """

    change_set: models.ChangeSet | None
    label: str
    user: str
    when: datetime.datetime
    changes: list[Change] = dataclasses.field(default_factory=list)
    other_cues: int = 0
    blocked: bool = False

    @property
    def can_revert(self) -> bool:
        """Whether there's a change set to undo, not undone or blocked."""
        return (
            self.change_set is not None
            and self.change_set.undone_at is None
            and not self.blocked
        )

    @property
    def undone(self) -> bool:
        """Whether this entry was later undone."""
        return (
            self.change_set is not None
            and self.change_set.undone_at is not None
        )


class _Names:
    """Looks up display names for the ids stored in history rows."""

    def __init__(self, cue: models.Cue, version_rows: Iterable[Any]) -> None:
        project = cue.project
        self.rate = project.rate
        self.statuses = dict(project.statuses.values_list("id", "label"))
        self.pictures = dict(project.pictures.values_list("id", "name"))
        self.sub_statuses = dict(
            project.sub_statuses.values_list("id", "label")
        )
        self.picture_versions = {
            pv.pk: f"{pv.picture.name} – {pv.turnover.label}"
            for pv in models.PictureVersion.objects.filter(
                picture__project=project
            ).select_related("picture", "turnover")
        }
        self.versions = {
            **dict(cue.versions.values_list("id", "label")),
            **{row.id: row.label for row in version_rows},
        }

    def display(
        self, model: type[db_models.Model], name: str, value: Any
    ) -> str:
        """A field value as the page shows it."""
        if value is None or value == "" or value == []:
            return "—"
        lookups = {
            "status": self.statuses,
            "picture": self.pictures,
            "written_to": self.picture_versions,
            "current_version": self.versions,
            "sub_status": self.sub_statuses,
        }
        if name in lookups:
            return str(lookups[name].get(value, "(deleted)"))
        if name in _TIMECODES:
            return timecode.format_timecode(value, self.rate)
        field = cast(
            db_models.Field,  # type: ignore[type-arg]
            model._meta.get_field(name),  # noqa: SLF001 pylint: disable=protected-access
        )
        if field.choices:
            return str(dict(field.choices).get(value, value))
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, datetime.datetime):
            return f"{timezone.localtime(value):%-d %b %Y %H:%M}"
        return str(value)


def _label(model: type[db_models.Model], name: str) -> str:
    if name in _LABELS:
        return _LABELS[name]
    field = cast(
        db_models.Field,  # type: ignore[type-arg]
        model._meta.get_field(name),  # noqa: SLF001 pylint: disable=protected-access
    )
    return capfirst(str(field.verbose_name))


def _changes(
    row: Any, previous: Any, subject: str, names: _Names
) -> list[Change]:
    model = row.instance_type
    if row.history_type == "+":
        return [Change(subject, "created")]
    if row.history_type == "-":
        return [Change(subject, "deleted")]
    if previous is None:
        return []
    delta = row.diff_against(previous)
    return [
        Change(
            subject,
            "changed",
            _label(model, change.field),
            names.display(model, change.field, change.old),
            names.display(model, change.field, change.new),
        )
        for change in delta.changes
        if change.field not in _SKIP
    ]


def _other_cues(cue: models.Cue, change_set_ids: set[int]) -> dict[int, int]:
    # pylint: disable=no-member  # simple_history's managers
    touched: dict[int, set[int]] = defaultdict(set)
    rows = list(
        models.Cue.history.filter(change_set_id__in=change_set_ids).values_list(
            "change_set_id", "id"
        )
    ) + list(
        models.CueVersion.history.filter(
            change_set_id__in=change_set_ids
        ).values_list("change_set_id", "cue_id")
    )
    for change_set_id, cue_id in rows:
        touched[change_set_id].add(cue_id)
    return {cs: len(ids - {cue.pk}) for cs, ids in touched.items()}


def _newest_rows(cue: models.Cue, limit: int) -> tuple[list[Any], list[Any]]:
    """The cue's and its versions' newest history rows, enough for `limit`.

    Every entry has at least one row, so this many of each is plenty,
    however long the history.
    """
    # pylint: disable=no-member  # simple_history's managers
    related = ("change_set__user", "history_user")
    window = limit * _ROWS_PER_ENTRY
    cue_rows = list(
        models.Cue.history.filter(id=cue.pk)
        .select_related(*related)
        .order_by("-history_id")[:window]
    )
    version_rows = list(
        models.CueVersion.history.filter(cue_id=cue.pk)
        .select_related(*related)
        .order_by("-history_id")[:window]
    )
    return cue_rows, version_rows


def _sequences(
    rows: list[Any],
) -> tuple[dict[int, Any], dict[tuple[str, int], list[Any]]]:
    """Each row's previous snapshot, and each object's rows, oldest first.

    An object's oldest row read diffs against the one before it, which
    the window may have left out, so that one is fetched.
    """
    previous: dict[int, Any] = {}
    by_object: dict[tuple[str, int], list[Any]] = defaultdict(list)
    for row in sorted(rows, key=lambda r: r.history_id):
        same = by_object[(row.instance_type.__name__, row.id)]
        if same:
            previous[id(row)] = same[-1]
        same.append(row)
    for same in by_object.values():
        first = same[0]
        if first.history_type == "~":
            previous[id(first)] = (
                first.instance_type.history.filter(
                    id=first.id, history_id__lt=first.history_id
                )
                .order_by("-history_id")
                .first()
            )
    return previous, by_object


# History rows read per entry shown (cue_history.entries).
_ROWS_PER_ENTRY = 5


def entries(cue: models.Cue, limit: int = 100) -> list[Entry]:
    """The cue's history, newest first.

    Args:
        cue: The cue.
        limit: Most entries to return.

    Returns:
        One entry per user action, each with its field changes.
    """
    cue_rows, version_rows = _newest_rows(cue, limit)
    names = _Names(cue, version_rows)
    previous, by_object = _sequences(cue_rows + version_rows)

    ordered = sorted(
        cue_rows + version_rows,
        key=lambda r: (r.history_date, r.change_seq or 0),
        reverse=True,
    )
    grouped: dict[Any, Entry] = {}
    rows_of: dict[Any, list[Any]] = defaultdict(list)
    for row in ordered:
        key = row.change_set_id or (
            "row",
            row.instance_type.__name__,
            row.history_id,
        )
        if key not in grouped:
            if len(grouped) == limit:
                break
            # The change set knows who acted even outside a web request.
            user = (
                row.change_set.user if row.change_set else None
            ) or row.history_user
            grouped[key] = Entry(
                change_set=row.change_set,
                label=row.change_set.label
                if row.change_set
                else _untracked(row),
                user=(user.get_full_name() or user.username) if user else "",
                when=row.history_date,
            )
        rows_of[key].append(row)
    for key, entry in grouped.items():
        entry.blocked = any(
            _changed_later(row, by_object) for row in rows_of[key]
        )
        for row in reversed(rows_of[key]):  # in the order they happened
            subject = "Cue" if row.instance_type is models.Cue else row.label
            entry.changes.extend(
                _changes(row, previous.get(id(row)), subject, names)
            )

    change_set_ids = {
        e.change_set.pk for e in grouped.values() if e.change_set is not None
    }
    others = _other_cues(cue, change_set_ids)
    for entry in grouped.values():
        if entry.change_set is not None:
            entry.other_cues = others.get(entry.change_set.pk, 0)
    return list(grouped.values())


def _changed_later(
    row: Any, by_object: dict[tuple[str, int], list[Any]]
) -> bool:
    """Whether another action changed the row's object afterwards.

    Mirrors `history.undo`'s own check for this cue's rows, so the page
    only offers Revert where it will work.
    """
    key = (row.instance_type.__name__, row.id)
    return any(
        later.history_id > row.history_id
        and later.change_set_id != row.change_set_id
        and not history.cancelled_out(later.change_set)
        for later in by_object[key]
    )


def _untracked(row: Any) -> str:
    noun = "cue" if row.instance_type is models.Cue else f"version {row.label}"
    verb = {"+": "Created", "-": "Deleted"}.get(row.history_type, "Changed")
    return f"{verb} {noun}"
