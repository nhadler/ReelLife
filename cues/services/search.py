"""The Ctrl+K palette's search.

Every word of the query must match somewhere: a project's title or
production number, a series' title, or a cue's number (from its start)
or title. So 'ABC101 1m01' finds 1m01 in project ABC101 and '1m01 main'
finds 1m01 Main Title. Cues rank exact numbers first, then numbers that
start with a word, then the project being looked at, then active cues.

A word may also match a cue's scene or notes (columns.SEARCHED_NOTES),
in any of its versions, not only the current one. Cues
found that way come after every number and title match, each with a
snippet of where it matched.
"""

import dataclasses
from functools import reduce
import operator

from django.db.models import Exists
from django.db.models import OuterRef
from django.db.models import Q
from django.db.models import prefetch_related_objects
from django.urls import reverse

from cues import columns
from cues import models

MAX_QUERY = 100
MAX_WORDS = 6
MAX_PROJECTS = 6
MAX_CUES = 12
# Candidates fetched before ranking; enough that the best are among them.
_CUE_CANDIDATES = 200
# Characters of context kept either side of a notes match.
_BEFORE = 24
_AFTER = 40

_CUE_NOTES = tuple(
    field
    for field in columns.SEARCHED_NOTES
    if columns.BY_ID[field].source == columns.Source.CUE
)
_VERSION_NOTES = tuple(
    field
    for field in columns.SEARCHED_NOTES
    if columns.BY_ID[field].source == columns.Source.VERSION
)


@dataclasses.dataclass(frozen=True)
class Hit:
    """One result: what to show and where it goes."""

    kind: str  # 'project' or 'cue'
    label: str
    detail: str
    url: str
    color: str = ""  # the cue's status color
    tag: str = ""  # 'Archived' or 'Inactive'
    number: str = ""  # a cue's number; then label is its title
    id: int = 0  # the project's or cue's
    # For a cue found by its notes: where, e.g. 'Spotting Notes (v1)',
    # and the text around the match, e.g. '…the door closes…'.
    field: str = ""
    snippet: str = ""


def words(query: str) -> list[str]:
    """The query's words, trimmed to a sane length."""
    return query[:MAX_QUERY].split()[:MAX_WORDS]


def _all(conditions: list[Q]) -> Q:
    return reduce(operator.and_, conditions, Q())


def _project_hit(project: models.Project) -> Hit:
    return Hit(
        kind="project",
        label=str(project),
        detail=project.get_kind_display(),
        url=reverse("cues:project", args=[project.pk]),
        tag="Archived" if project.archived else "",
        id=project.pk,
    )


def projects(query: str) -> list[Hit]:
    """Projects matching every word; with no words, the latest touched."""
    found = models.Project.objects.select_related("series")
    terms = words(query)
    if terms:
        found = found.filter(
            _all(
                [
                    Q(title__icontains=term)
                    | Q(production_number__icontains=term)
                    | Q(series__title__icontains=term)
                    for term in terms
                ]
            )
        )
    found = found.order_by("archived", "-updated_at")
    return [_project_hit(project) for project in found[:MAX_PROJECTS]]


_Rank = tuple[bool, bool, bool, bool, bool, str, int, str]


def _rank(cue: models.Cue, terms: list[str], project_id: int | None) -> _Rank:
    number = cue.number.lower()
    lowered = [term.lower() for term in terms]
    return (
        not any(number == term for term in lowered),
        not any(number.startswith(term) for term in lowered),
        cue.project_id != project_id,
        not cue.active,
        cue.project.archived,
        cue.project.title.lower(),
        cue.tc_in if cue.tc_in is not None else 0,
        number,
    )


def _named(term: str) -> Q:
    """Cues whose number, title or project has the word."""
    return (
        Q(number__istartswith=term)
        | Q(title__icontains=term)
        | Q(project__production_number__iexact=term)
        | Q(project__title__icontains=term)
        | Q(project__series__title__icontains=term)
    )


def _any(fields: tuple[str, ...], term: str) -> Q:
    return reduce(
        operator.or_, [Q(**{f"{field}__icontains": term}) for field in fields]
    )


def _noted(term: str) -> Q:
    """Cues whose scene or notes, or any version's, have the word."""
    versions = models.CueVersion.objects.filter(
        _any(_VERSION_NOTES, term), cue=OuterRef("pk")
    )
    return _any(_CUE_NOTES, term) | Q(Exists(versions))


def _is_named(cue: models.Cue, term: str) -> bool:
    """Whether `_named(term)` holds for this cue."""
    word = term.lower()
    project = cue.project
    series = project.series.title if project.series else ""
    return (
        cue.number.lower().startswith(word)
        or word in cue.title.lower()
        or project.production_number.lower() == word
        or word in project.title.lower()
        or word in series.lower()
    )


def snippet(text: str, term: str) -> str:
    """A line of `text` around the first `term`, cut between words.

    Args:
        text: Notes, perhaps over several lines.
        term: The word that matched, in any case.

    Returns:
        The text around it on one line, with '…' where it was cut.
    """
    flat = " ".join(text.split())
    at = max(flat.lower().find(term.lower()), 0)
    start = max(at - _BEFORE, 0)
    end = min(at + len(term) + _AFTER, len(flat))
    if start > 0 and " " in flat[start:at]:
        start = flat.index(" ", start) + 1
    if end < len(flat) and " " in flat[at + len(term) : end]:
        end = flat.rindex(" ", 0, end)
    return (
        ("…" if start > 0 else "")
        + flat[start:end]
        + ("…" if end < len(flat) else "")
    )


_Place = tuple[models.CueVersion | None, str]  # (None: the cue), field


def _places(cue: models.Cue) -> list[_Place]:
    """Where to look for a match, in order.

    The cue and its current version in SEARCHED_NOTES order, then each
    older version, newest first.
    """
    versions = list(cue.versions.all())  # oldest first
    current = next(
        (v for v in versions if v.pk == cue.current_version_id), None
    )
    places: list[_Place] = [
        (None if field in _CUE_NOTES else current, field)
        for field in columns.SEARCHED_NOTES
        if field in _CUE_NOTES or current is not None
    ]
    places += [
        (version, field)
        for version in reversed(versions)
        if version.pk != cue.current_version_id
        for field in _VERSION_NOTES
    ]
    return places


def _cue_hit(cue: models.Cue, terms: list[str] | None = None) -> Hit:
    """A cue result; given `terms`, one found by its notes.

    The snippet is for the first word the cue's number, title and
    project don't explain. A match in an older version links to the cue
    page showing that version.
    """
    field, text, older = "", "", 0
    if terms:
        wanted = [t for t in terms if not _is_named(cue, t)] or terms
        found = [
            (version, name, str(getattr(version or cue, name)), term)
            for version, name in _places(cue)
            for term in wanted
            if term.lower() in str(getattr(version or cue, name)).lower()
        ]
        # The database matched, so something here does; [] only if the
        # database and Python disagree about case.
        version, name, notes, term = (found or [(None, "", "", "")])[0]
        field = columns.BY_ID[name].label if name else ""
        if version is not None:
            field += f" ({version.label})"
            older = 0 if version.pk == cue.current_version_id else version.pk
        text = snippet(notes, term) if name else ""
    url = reverse("cues:cue", args=[cue.pk])
    return Hit(
        kind="cue",
        label=cue.title,
        number=cue.number,
        detail=str(cue.project),
        url=f"{url}?version={older}" if older else url,
        color=cue.status.color if cue.status else "",
        tag="" if cue.active else "Inactive",
        id=cue.pk,
        field=field,
        snippet=text,
    )


def cues(query: str, project_id: int | None = None) -> list[Hit]:
    """Cues matching every word, best first; notes matches after."""
    terms = words(query)
    if not terms:
        return []
    every = models.Cue.objects.select_related("project__series", "status")
    found = every.filter(_all([_named(term) for term in terms]))
    # Exact numbers are fetched on their own so the cap can't drop them.
    exact = found.filter(
        reduce(operator.or_, [Q(number__iexact=term) for term in terms])
    )
    candidates = {cue.pk: cue for cue in exact[:_CUE_CANDIDATES]}
    for cue in found[:_CUE_CANDIDATES]:
        candidates.setdefault(cue.pk, cue)
    ranked = sorted(
        candidates.values(), key=lambda cue: _rank(cue, terms, project_id)
    )
    hits = [_cue_hit(cue) for cue in ranked[:MAX_CUES]]
    room = MAX_CUES - len(hits)
    if not room:
        return hits
    # Fewer than MAX_CUES named matches, so `candidates` holds them all.
    noted = every.filter(
        _all([_named(term) | _noted(term) for term in terms])
    ).exclude(pk__in=list(candidates))
    more = sorted(
        noted[:_CUE_CANDIDATES], key=lambda cue: _rank(cue, terms, project_id)
    )[:room]
    prefetch_related_objects(more, "versions")
    return hits + [_cue_hit(cue, terms) for cue in more]
