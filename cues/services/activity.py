"""Change sets as lists: a project's Activity and Credits' recent changes.

Each entry says who did what and when, how many cues it touched, and
whether it can be undone now, by the same rule `history.undo` applies:
nothing it touched was changed afterwards, except by changes that
cancel out (an undo and what it undid). Undoing an undo re-applies the
original, so that is offered as Redo.
"""

# pylint: disable=no-member  # simple_history's managers

from collections import defaultdict
from collections.abc import Iterable, Sequence
import dataclasses
import datetime
from typing import Any

from django.db import models as db_models

from accounts.models import User
from cues import models
from cues.services import history

# Rows per query when looking ids up (SQLite's variable limit).
_CHUNK = 500

# The field of each tracked model's history that names its cue.
_CUE_FIELD = {
    models.Cue: "id",
    models.CueVersion: "cue_id",
    models.CueShare: "cue_id",
    models.CueInstrument: "cue_id",
}
# Tracked models that name a version instead; its cue is looked up.
_BY_VERSION = (models.VersionTheme, models.ReviewComment)


@dataclasses.dataclass
class Entry:
    """One change set as a list shows it.

    Attributes:
        change_set: The change set.
        user: Who made it ("" when unknown).
        cues: How many cues it touched.
        action: "undo" or "redo" when that would work now, else "".
        blocked: Something it touched was changed afterwards.
    """

    change_set: models.ChangeSet
    user: str
    cues: int = 0
    action: str = ""
    blocked: bool = False

    @property
    def label(self) -> str:
        """What was done."""
        return self.change_set.label

    @property
    def when(self) -> datetime.datetime:
        """When it was done."""
        return self.change_set.created_at

    @property
    def undone(self) -> bool:
        """Whether it was undone since."""
        return self.change_set.undone_at is not None


def user_name(user: User | None) -> str:
    """A user's name as lists show it."""
    if user is None:
        return ""
    return user.get_full_name() or user.username


def _chunks(ids: Iterable[int]) -> Iterable[list[int]]:
    ordered = sorted(set(ids))
    for start in range(0, len(ordered), _CHUNK):
        yield ordered[start : start + _CHUNK]


def _load_chains(change_sets: Iterable[models.ChangeSet]) -> None:
    """Loads every change set's undo_of chain, so walking it is free."""
    loaded = {cs.pk: cs for cs in change_sets}
    waiting = list(loaded.values())
    while waiting:
        wanted = {
            cs.undo_of_id
            for cs in waiting
            if cs.undo_of_id is not None and cs.undo_of_id not in loaded
        }
        for chunk in _chunks(wanted):
            loaded.update(models.ChangeSet.objects.in_bulk(chunk))
        for cs in waiting:
            if cs.undo_of_id is not None:
                cs.undo_of = loaded.get(cs.undo_of_id)
        waiting = [loaded[pk] for pk in wanted if pk in loaded]


def _depth(change_set: models.ChangeSet) -> int:
    depth = 0
    undone = change_set.undo_of
    while undone is not None:
        depth += 1
        undone = undone.undo_of
    return depth


def _history(model: type[db_models.Model]) -> Any:
    return getattr(model, "history")  # noqa: B009


def _touched(
    ids: list[int],
) -> tuple[dict[tuple[Any, int], dict[int, int]], dict[int, set[int]]]:
    """What each change set touched.

    Returns:
        For each (model, object id), the latest history id per change
        set; and the cue ids per change set.
    """
    last: dict[tuple[Any, int], dict[int, int]] = defaultdict(dict)
    cues: dict[int, set[int]] = defaultdict(set)
    versioned: list[tuple[int, int]] = []
    for model in history.TRACKED:
        cue_field = _CUE_FIELD.get(model)
        extra = cue_field or ("version_id" if model in _BY_VERSION else "id")
        rows = _history(model).filter(change_set_id__in=ids)
        for cs_id, pk, history_id, related in rows.values_list(
            "change_set_id", "id", "history_id", extra
        ):
            sets = last[(model, pk)]
            sets[cs_id] = max(sets.get(cs_id, 0), history_id)
            if cue_field:
                cues[cs_id].add(related)
            elif model in _BY_VERSION:
                versioned.append((cs_id, related))
    version_cues: dict[int, int] = {}
    for chunk in _chunks(version for _, version in versioned):
        version_cues.update(
            _history(models.CueVersion)
            .filter(id__in=chunk)
            .values_list("id", "cue_id")
        )
    for cs_id, version in versioned:
        # A version with no history (made outside the app) has no cue here.
        if version in version_cues:
            cues[cs_id].add(version_cues[version])
    return last, cues


def _blocked(last: dict[tuple[Any, int], dict[int, int]]) -> set[int]:
    """The change sets something changed after (history.undo's check)."""
    later: dict[tuple[Any, int], list[tuple[int, int | None]]] = {}
    by_model: dict[Any, dict[int, int]] = defaultdict(dict)
    for (model, pk), sets in last.items():
        by_model[model][pk] = min(sets.values())
    for model, objects in by_model.items():
        for chunk in _chunks(objects):
            rows = _history(model).filter(
                id__in=chunk, history_id__gt=min(objects[pk] for pk in chunk)
            )
            for pk, history_id, cs_id in rows.values_list(
                "id", "history_id", "change_set_id"
            ):
                later.setdefault((model, pk), []).append((history_id, cs_id))
    others = {
        cs_id
        for rows in later.values()
        for _, cs_id in rows
        if cs_id is not None
    }
    loaded: dict[int, models.ChangeSet] = {}
    for chunk in _chunks(others):
        loaded.update(models.ChangeSet.objects.in_bulk(chunk))
    _load_chains(loaded.values())
    blocked: set[int] = set()
    for key, sets in last.items():
        for cs_id, history_id in sets.items():
            if any(
                later_id > history_id
                and not history.cancelled_out(
                    loaded[other] if other is not None else None
                )
                for later_id, other in later.get(key, ())
            ):
                blocked.add(cs_id)
    return blocked


def entries(change_sets: Sequence[models.ChangeSet]) -> list[Entry]:
    """The change sets as entries, in the order given.

    Args:
        change_sets: Change sets, with their users loaded.

    Returns:
        One entry each.
    """
    ids = [cs.pk for cs in change_sets]
    last, cues = _touched(ids)
    blocked = _blocked(last)
    _load_chains(change_sets)
    result = []
    for cs in change_sets:
        entry = Entry(
            change_set=cs,
            user=user_name(cs.user),
            cues=len(cues.get(cs.pk, ())),
            blocked=cs.pk in blocked,
        )
        if cs.undone_at is None and not entry.blocked:
            entry.action = "redo" if _depth(cs) % 2 else "undo"
        result.append(entry)
    return result
