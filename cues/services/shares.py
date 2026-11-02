"""Writer and publisher shares: checks and project defaults.

Writer shares (C, A, CA, AR, AD) must add up to 100%, and publisher
shares (E, SE, AM) too when there are any; a writer party takes writer
roles and a publisher party publisher roles. New compositions get the
project's default shares; songs and licensed music don't, since someone
else wrote them.
"""

from collections.abc import Iterable, Sequence
import dataclasses
from decimal import Decimal
import hashlib
from typing import Any, Protocol, cast

from accounts.models import User
from cues import models
from cues.services import history
from cues.shares import PUBLISHER_ROLES
from cues.shares import WRITER_ROLES

HUNDRED = Decimal(100)


class Named(Protocol):
    """What the rules need of a party: its name and kind."""

    @property
    def name(self) -> str:
        """As the PRO knows it."""

    @property
    def kind(self) -> str:
        """'writer' or 'publisher'."""


@dataclasses.dataclass(frozen=True)
class Line:
    """A share as entered: a party, a role and a percentage."""

    party: Named
    role: str
    share: Decimal


def problems(lines: Iterable[Line]) -> list[str]:
    """What's wrong with a cue's (or a project's default) shares.

    Returns:
        Plain sentences; empty when the shares are fine (or empty).
    """
    lines = list(lines)
    if not lines:
        return []
    found = []
    for line in lines:
        writer_role = line.role in WRITER_ROLES
        if writer_role != (line.party.kind == models.Party.Kind.WRITER):
            kind = "writer" if writer_role else "publisher"
            found.append(
                f"{line.party.name} isn't a {kind}, so can't be {line.role}."
            )
    writers = sum(
        (line.share for line in lines if line.role in WRITER_ROLES), Decimal(0)
    )
    publishers = [line.share for line in lines if line.role in PUBLISHER_ROLES]
    if not writers:
        found.append("Add at least one writer.")
    elif writers != HUNDRED:
        found.append(f"Writer shares add up to {_percent(writers)}, not 100%.")
    published = sum(publishers, Decimal(0))
    if publishers and published != HUNDRED:
        found.append(
            f"Publisher shares add up to {_percent(published)}, not 100%."
        )
    return found


def _percent(value: Decimal) -> str:
    return f"{value.normalize():f}%"


def is_composition(cue: models.Cue) -> bool:
    """Music the composer writes: score, not a song, licensed or omitted.

    Song and Source cue types never count, whatever their status: a
    third-party song left at To Write mustn't pick up the composer's
    default shares.
    """
    if cue.cue_type in (models.Cue.CueType.SONG, models.Cue.CueType.SOURCE):
        return False
    return cue.status is None or cue.status.category in (
        "unapproved",
        "approved",
    )


def default_shares(project: models.Project) -> list[Any]:
    """The project's default shares, or its series' if it has none."""
    own = list(project.default_shares.select_related("party"))
    if own or project.series is None:
        return own
    return list(project.series.default_shares.select_related("party"))


def give_defaults(
    cue: models.Cue, defaults: Sequence[Any] | None = None
) -> int:
    """Gives a new composition the project's (or series') default shares.

    Runs inside the caller's change set. Cues that already have shares,
    and songs or licensed music, are left alone.

    Args:
        cue: The new cue.
        defaults: default_shares(cue.project), when the caller already
            has them (an import gives many cues the same ones).

    Returns:
        How many shares were added.
    """
    # Ask the database: a caller may hold stale prefetched shares.
    if (
        not is_composition(cue)
        or models.CueShare.objects.filter(cue=cue).exists()
    ):
        return 0
    if defaults is None:
        defaults = default_shares(cue.project)
    for default in defaults:  # one by one, so history records each
        models.CueShare.objects.create(
            cue=cue,
            party=default.party,
            role=default.role,
            share=default.share,
            order=default.order,
        )
    return len(defaults)


def apply_defaults(
    project: models.Project, *, replace: bool = False, user: User | None = None
) -> int:
    """Gives the project's compositions its default shares.

    Args:
        project: The project.
        replace: Also replace the shares compositions already have;
            otherwise only cues without any get them.
        user: Who asked.

    Returns:
        How many cues changed. One change set, so Undo reverts all.
    """
    cues = [
        cue
        for cue in project.cues.select_related("status").prefetch_related(
            "shares"
        )
        if is_composition(cue) and (replace or not cue.shares.all())
    ]
    if not cues or not default_shares(project):
        return 0
    with history.recording(
        project,
        f"Default shares on {len(cues)} cue{'s' if len(cues) != 1 else ''}",
        user,
    ):
        for cue in cues:
            for share in cue.shares.all():
                share.delete()
            give_defaults(cue)
    return len(cues)


def revision(cue: models.Cue) -> str:
    """A short fingerprint of a cue's shares as they stand.

    The Credits section posts it back, so a save made from a stale page
    (someone else saved in between) is refused rather than replacing
    their split. Saving replaces every row and doesn't touch the cue, so
    neither row ids nor the cue's updated_at can show this.
    """
    rows = sorted(
        (share.party_id, share.role, str(share.share), share.order)
        for share in cue.shares.all()
    )
    return hashlib.sha256(repr(rows).encode()).hexdigest()[:16]


def set_shares(
    cue: models.Cue, lines: list[Line], user: User | None = None
) -> None:
    """Replaces a cue's shares, as one undoable change."""
    with history.recording(cue.project, f"Edit {cue.number} Shares", user):
        for share in cue.shares.all():
            share.delete()
        for order, line in enumerate(lines):
            models.CueShare.objects.create(
                cue=cue,
                party=cast(models.Party, line.party),  # from the form
                role=line.role,
                share=line.share,
                order=order,
            )
