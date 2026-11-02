"""The overview's cue tools.

Each tool has a plan step, which works out what would change without
touching the database (the dialogs show it as a preview), and an apply
step that runs the plan as one change set, so Undo reverts all of it.
Plans refuse what the database would refuse (a number in use, a time
before 0:00:00:00) with ToolError, naming the cues involved.
"""

from collections.abc import Iterable, Sequence
import dataclasses

from django.db import models as db_models

from accounts.models import User
from cues import models
from cues import numbering
from cues import timecode
from cues.services import authoring
from cues.services import history


class ToolError(ValueError):
    """A tool can't run as asked; the message says why, for the user."""


def _taken(project: models.Project, *, besides: Iterable[int] = ()) -> set[str]:
    """Active cue numbers in the project, lowercased, but `besides`'."""
    return {
        number.lower()
        for number in project.cues.filter(active=True)
        .exclude(pk__in=list(besides))
        .values_list("number", flat=True)
    }


def _check_numbers(
    project: models.Project,
    numbers: Sequence[str],
    taken: set[str],
    *,
    kept: Iterable[str] = (),
) -> None:
    """Refuses blank, repeated, taken or invalid numbers.

    Numbers in `kept` (lowercased) are cues keeping their own, which
    needn't fit Valid Cue Numbers (numbering.misfit).

    Raises:
        ToolError: Naming the first few bad numbers.
    """
    seen: set[str] = set()
    kept = set(kept)
    problems = []
    for number in numbers:
        key = number.strip().lower()
        if not key:
            problems.append("a blank number")
        elif key in seen:
            problems.append(f"{number} twice")
        elif key in taken:
            problems.append(f"{number} (in use)")
        elif key not in kept and not numbering.is_valid(
            number, project.cue_number_pattern
        ):
            problems.append(f"{number} (doesn't fit Valid Cue Numbers)")
        seen.add(key)
    if problems:
        raise ToolError("Can't use " + ", ".join(problems[:5]) + ".")


def _cue_noun(count: int) -> str:
    return "1 cue" if count == 1 else f"{count} cues"


def _composed_status(project: models.Project) -> models.StatusOption | None:
    return project.statuses.filter(kind=models.StatusKind.DONE).first()


def _is_composition(cue: models.Cue) -> bool:
    return cue.status is None or cue.status.kind not in (
        models.StatusKind.SONG,
        models.StatusKind.LICENSE,
    )


# New cues ---------------------------------------------------------------


def plan_new_cues(picture: models.Picture, first: str, count: int) -> list[str]:
    """The numbers a batch of new cues would get.

    `first`, then up by one, keeping its suffix (1m05s, 1m06s …).

    Raises:
        ToolError: A number can't be made, is in use or is invalid.
    """
    numbers = []
    for step in range(count):
        number = numbering.step_number(first.strip(), step)
        if number is None:
            raise ToolError(f"{first} has no digits to count up from.")
        numbers.append(number)
    project = picture.project
    _check_numbers(project, numbers, _taken(project))
    return numbers


def new_cues(
    picture: models.Picture,
    numbers: Sequence[str],
    *,
    status: models.StatusOption | None,
    user: User | None = None,
) -> list[models.Cue]:
    """Creates a cue with a v1 for each number, in one change set."""
    project = picture.project
    label = f"New cues {numbers[0]}–{numbers[-1]}"
    with history.recording(project, label, user):
        return [
            authoring.save_with_v1(
                models.Cue(
                    project=project,
                    picture=picture,
                    number=number,
                    status=status,
                )
            )
            for number in numbers
        ]


# Add versions -------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class VersionOptions:
    """How Add versions makes each cue's next version."""

    minor: bool = False
    keep_notes: bool = True
    keep_sub_status: bool = True
    mark_composed: bool = True


def compositions_to_mark(cues: Sequence[models.Cue]) -> list[models.Cue]:
    """The compositions Add versions would mark Composed.

    Those not already Composed: the k in the dialog's checkbox.
    """
    if not cues:
        return []
    composed = _composed_status(cues[0].project)
    if composed is None:
        return []
    return [
        cue
        for cue in cues
        if _is_composition(cue) and cue.status_id != composed.pk
    ]


def next_label(cue: models.Cue, *, minor: bool) -> str:
    """The label a cue's next version gets."""
    labels = list(cue.versions.values_list("label", flat=True))
    return numbering.next_version_label(labels, minor=minor)


def add_versions(
    cues: Sequence[models.Cue],
    options: VersionOptions,
    *,
    user: User | None = None,
) -> list[models.CueVersion]:
    """Gives each cue a new current version.

    Written to the reel's current picture, at the cue's current
    timecodes; notes and sub status carry over when asked, revision and
    change notes start blank, themes carry over. Approved cues reopen
    review (authoring.reopened_status).
    """
    project = cues[0].project
    composed = _composed_status(project) if options.mark_composed else None
    to_mark = {cue.pk for cue in compositions_to_mark(cues)}
    created = []
    with history.recording(
        project, f"Add versions to {_cue_noun(len(cues))}", user
    ):
        for cue in cues:
            previous = cue.current_version
            version = models.CueVersion(
                cue=cue,
                label=next_label(cue, minor=options.minor),
                written_to=cue.picture.current_version(),
                tc_in=cue.tc_in,
                tc_out=cue.tc_out,
                seq_start=cue.seq_start,
            )
            if previous is not None:
                if options.keep_notes:
                    for name in models.VERSION_NOTE_FIELDS:
                        setattr(version, name, getattr(previous, name))
                if options.keep_sub_status:
                    version.sub_status = previous.sub_status
            status = (
                composed
                if composed is not None and cue.pk in to_mark
                else authoring.reopened_status(cue)
            )
            created.append(
                authoring.save_version(version, update_cue=False, status=status)
            )
    return created


# Split ----------------------------------------------------------------------


def _span(cue: models.Cue, doing: str) -> tuple[int, int]:
    """A cue's IN and OUT.

    Raises:
        ToolError: It lacks one.
    """
    if cue.tc_in is None or cue.tc_out is None:
        raise ToolError(f"{cue.number} needs an IN and an OUT to {doing}.")
    return cue.tc_in, cue.tc_out


@dataclasses.dataclass(frozen=True)
class SplitPlan:
    """Where a cue splits and which half becomes the new cue."""

    cue: models.Cue
    span: tuple[int, int]
    at: int
    number: str
    new_takes_second: bool = True

    @property
    def first(self) -> tuple[int, int]:
        """IN and OUT of the first half."""
        return self.span[0], self.at

    @property
    def second(self) -> tuple[int, int]:
        """IN and OUT of the second half."""
        return self.at, self.span[1]


def split_defaults(cue: models.Cue) -> tuple[int, str]:
    """The suggested split point (the midpoint, rounded down) and number.

    Raises:
        ToolError: The cue has no IN and OUT to split between.
    """
    tc_in, tc_out = _span(cue, "split")
    taken = _taken(cue.project)
    return tc_in + (tc_out - tc_in) // 2, numbering.split_number(
        cue.number, taken
    )


def plan_split(
    cue: models.Cue, at: int, number: str, *, new_takes_second: bool = True
) -> SplitPlan:
    """Checks a split.

    Raises:
        ToolError: No IN/OUT, a point outside the cue, or a bad number.
    """
    span = _span(cue, "split")
    if not span[0] < at < span[1]:
        raise ToolError("The split point must fall inside the cue.")
    _check_numbers(cue.project, [number.strip()], _taken(cue.project))
    return SplitPlan(cue, span, at, number.strip(), new_takes_second)


def split(plan: SplitPlan, *, user: User | None = None) -> models.Cue:
    """Splits a cue in two.

    The original keeps its number, versions and history, trimmed to one
    half; the new cue takes the other with a v1. Seq. start stays with
    the half that starts at the original IN.

    Returns:
        The new cue.
    """
    cue = plan.cue
    kept, given = (
        (plan.first, plan.second)
        if plan.new_takes_second
        else (plan.second, plan.first)
    )
    new = models.Cue(
        project=cue.project,
        picture=cue.picture,
        number=plan.number,
        title=cue.title,
        status=cue.status,
        cue_type=cue.cue_type,
        lane=cue.lane,
        priority=cue.priority,
        difficulty=cue.difficulty,
        tc_in=given[0],
        tc_out=given[1],
        seq_start=None if plan.new_takes_second else cue.seq_start,
    )
    with history.recording(
        cue.project, f"Split {cue.number} into {plan.number}", user
    ):
        cue.tc_in, cue.tc_out = kept
        if not plan.new_takes_second:
            cue.seq_start = None
        cue.full_clean()
        cue.save()
        authoring.save_with_v1(new)
        _copy_shares(cue, new)
    return new


def _copy_shares(source: models.Cue, cue: models.Cue) -> None:
    """Gives a cue the source's writers and publishers (split, copy).

    Replaces any defaults the new cue was given; a source without shares
    leaves them.
    """
    shares = list(source.shares.all())
    if not shares:
        return
    for share in cue.shares.all():
        share.delete()
    for share in shares:
        models.CueShare.objects.create(
            cue=cue,
            party_id=share.party_id,
            role=share.role,
            share=share.share,
            order=share.order,
        )


# Merge ----------------------------------------------------------------------


def merge_defaults(cues: Sequence[models.Cue]) -> tuple[int, int, models.Cue]:
    """IN of the earliest cue, OUT of the latest, and the earliest cue.

    Raises:
        ToolError: Fewer than two cues, or one without IN and OUT.
    """
    if len(cues) < 2:
        raise ToolError("Pick two or more cues to merge.")
    spans = {cue.pk: _span(cue, "merge") for cue in cues}
    earliest = min(cues, key=lambda cue: (spans[cue.pk][0], cue.pk))
    latest_out = max(span[1] for span in spans.values())
    return spans[earliest.pk][0], latest_out, earliest


def merge(
    cues: Sequence[models.Cue],
    keeper: models.Cue,
    span: tuple[int, int],
    *,
    delete_others: bool = False,
    user: User | None = None,
) -> models.Cue:
    """Merges cues into `keeper`.

    Only the keeper's IN and OUT change. The others are marked Omit and
    made inactive, or deleted.

    Raises:
        ToolError: OUT not after IN, or a keeper not among the cues.
    """
    merge_defaults(cues)
    if keeper not in cues:
        raise ToolError("The cue to keep must be one of those merged.")
    if span[1] <= span[0]:
        raise ToolError("OUT must be after IN.")
    others = [cue for cue in cues if cue.pk != keeper.pk]
    omit = keeper.project.statuses.filter(kind=models.StatusKind.OMIT).first()
    with history.recording(
        keeper.project,
        f"Merge {_cue_noun(len(cues))} into {keeper.number}",
        user,
    ):
        keeper.tc_in, keeper.tc_out = span
        keeper.full_clean()
        keeper.save()
        for cue in others:
            if delete_others:
                _delete(cue)
            else:
                cue.active = False
                if omit is not None:
                    cue.status = omit
                cue.save()
    return keeper


# Renumber -------------------------------------------------------------------


class ReelPart(db_models.TextChoices):
    """Where an overwritten number's reel part ('1' in 1m05) comes from."""

    KEEP = "keep", "Keep the Start Number's"
    CUE_REEL = "reel", "The Cue's Reel"
    HOUR = "hour", "The Hour of Its IN"


def bump(numbers: Sequence[str], index: int, by: int) -> list[str]:
    """Moves the number at `index` and every one after it by `by`.

    Gaps, prefixes and suffixes stay (the renumber list's −/+).

    Numbers that can't move (no digits, or below 0) stay as they are.
    """
    return [
        (numbering.step_number(number, by) or number) if i >= index else number
        for i, number in enumerate(numbers)
    ]


def overwrite_numbers(
    cues: Sequence[models.Cue],
    start: str,
    *,
    keep_suffix: bool = True,
    reel: ReelPart = ReelPart.KEEP,
) -> list[str]:
    """Numbers from `start` up by one per cue with no gaps.

    Args:
        cues: The cues in order.
        start: The first number.
        keep_suffix: Each cue keeps its own suffix ('1m04a'); otherwise
            every number takes the start number's.
        reel: Where the reel part comes from.

    Raises:
        ToolError: The start number has no digits.
    """
    numbers = []
    for step, cue in enumerate(cues):
        number = numbering.step_number(start.strip(), step)
        if number is None:
            raise ToolError(f"{start} has no digits to count up from.")
        if keep_suffix:
            number = numbering.with_suffix(
                number, numbering.suffix_of(cue.number)
            )
        if reel == ReelPart.CUE_REEL:
            number = numbering.with_reel(number, cue.picture.name)
        elif reel == ReelPart.HOUR and cue.tc_in is not None:
            hour = timecode.hours(cue.tc_in, cue.project.rate)
            number = numbering.with_reel(number, str(hour))
        numbers.append(number)
    return numbers


def check_renumber(cues: Sequence[models.Cue], numbers: Sequence[str]) -> None:
    """Refuses numbers in use outside the selection, repeated or invalid.

    Raises:
        ToolError: Naming them.
    """
    project = cues[0].project
    taken = _taken(project, besides=[cue.pk for cue in cues])
    kept = [
        cue.number.strip().lower()
        for cue, number in zip(cues, numbers, strict=True)
        if cue.number.strip() == number.strip()
    ]
    _check_numbers(project, [n.strip() for n in numbers], taken, kept=kept)


def renumber(
    cues: Sequence[models.Cue],
    numbers: Sequence[str],
    *,
    user: User | None = None,
) -> None:
    """Gives each cue its new number, in one change set.

    Numbers may swap within the selection: each cue first takes a
    placeholder so no two active cues ever share a number.
    """
    check_renumber(cues, numbers)
    changes = [
        (cue, number.strip())
        for cue, number in zip(cues, numbers, strict=True)
        if cue.number != number.strip()
    ]
    if not changes:
        return
    with history.recording(
        cues[0].project, f"Renumber {_cue_noun(len(changes))}", user
    ):
        for cue, _ in changes:
            cue.number = f"~{cue.pk}"
            cue.save(update_fields=["number", "updated_at"])
        for cue, number in changes:
            cue.number = number
            cue.save(update_fields=["number", "updated_at"])


# Shift ----------------------------------------------------------------------


def shifted(cue: models.Cue, delta: int) -> tuple[int | None, ...]:
    """A cue's IN, OUT and Seq. start moved by `delta` frames."""
    return tuple(
        None if value is None else value + delta
        for value in (cue.tc_in, cue.tc_out, cue.seq_start)
    )


def check_shift(cues: Sequence[models.Cue], delta: int) -> None:
    """Refuses a shift that moves a time outside the timecode range.

    Raises:
        ToolError: Naming the cues.
    """
    if not cues:
        return
    rate = cues[0].project.rate
    limit = timecode.max_frames(rate)
    bad = [
        cue.number
        for cue in cues
        if any(
            value is not None and not 0 <= value <= limit
            for value in shifted(cue, delta)
        )
    ]
    if bad:
        raise ToolError(
            f"That moves {', '.join(bad[:5])} outside 0:00:00:00–"
            f"{timecode.format_timecode(limit, rate)}."
        )


def shift(
    cues: Sequence[models.Cue], delta: int, *, user: User | None = None
) -> None:
    """Moves the cues' IN, OUT and Seq. start by `delta` frames.

    Their versions keep their timecodes.
    """
    check_shift(cues, delta)
    if not delta:
        return
    rate = cues[0].project.rate
    label = (
        f"Shift {_cue_noun(len(cues))} by {timecode.format_offset(delta, rate)}"
    )
    with history.recording(cues[0].project, label, user):
        for cue in cues:
            cue.tc_in, cue.tc_out, cue.seq_start = shifted(cue, delta)
            cue.full_clean()
            cue.save()


# Move and copy --------------------------------------------------------------


def move(
    cues: Sequence[models.Cue],
    picture: models.Picture,
    *,
    user: User | None = None,
) -> None:
    """Moves the cues to another reel; timecodes stay."""
    moving = [cue for cue in cues if cue.picture_id != picture.pk]
    if not moving:
        return
    with history.recording(
        picture.project,
        f"Move {_cue_noun(len(moving))} to reel {picture.name}",
        user,
    ):
        for cue in moving:
            cue.picture = picture
            cue.full_clean()
            cue.save()


def copy_numbers(
    cues: Sequence[models.Cue], picture: models.Picture
) -> list[str]:
    """The next free numbers in `picture`, one per cue, in order.

    Raises:
        ToolError: One doesn't fit Valid Cue Numbers.
    """
    project = picture.project
    taken = _taken(project)
    in_picture = list(picture.cues.values_list("number", flat=True))
    numbers = []
    for _ in cues:
        number = numbering.suggest_number(in_picture, picture.name, taken)
        numbers.append(number)
        in_picture.append(number)
        taken.add(number.lower())
    _check_numbers(project, numbers, set())
    return numbers


_NOT_COPIED = {"id", "project", "picture", "number", "current_version"}
_TIMESTAMPS = {"created_at", "updated_at"}


def copy(
    cues: Sequence[models.Cue],
    picture: models.Picture,
    *,
    all_versions: bool = False,
    user: User | None = None,
) -> list[models.Cue]:
    """Copies the cues into a reel with the next free numbers there.

    The copies take every field but history, and their current version
    (or all versions), written to the reel's current picture; themes and
    instrumentation come along.
    """
    numbers = copy_numbers(cues, picture)
    written_to = picture.current_version()
    copies = []
    with history.recording(
        picture.project,
        f"Copy {_cue_noun(len(cues))} to reel {picture.name}",
        user,
    ):
        for cue, number in zip(cues, numbers, strict=True):
            new = models.Cue(
                project=picture.project,
                picture=picture,
                number=number,
                **_fields(cue, _NOT_COPIED),
            )
            new.full_clean()
            new.save()
            _copy_shares(cue, new)
            for link in cue.instrumentation.all():
                models.CueInstrument.objects.create(
                    cue=new,
                    instrument_id=link.instrument_id,
                    seconds=link.seconds,
                    order=link.order,
                )
            versions = (
                list(cue.versions.order_by("created_at", "id"))
                if all_versions
                else [v for v in [cue.current_version] if v is not None]
            )
            for version in versions:
                copied = _copy_version(version, new, written_to)
                if version.pk == cue.current_version_id:
                    new.current_version = copied
            new.save()
            copies.append(new)
    return copies


def _fields(
    instance: db_models.Model, leave_out: set[str]
) -> dict[str, object]:
    """A row's own field values by column name.

    Leaves out the fields named and the timestamps.
    """
    return {
        field.attname: getattr(instance, field.attname)
        for field in type(instance)._meta.concrete_fields  # noqa: SLF001
        if field.name not in leave_out | _TIMESTAMPS
    }


def _copy_version(
    version: models.CueVersion,
    cue: models.Cue,
    written_to: models.PictureVersion | None,
) -> models.CueVersion:
    copied = models.CueVersion(
        cue=cue,
        written_to=written_to,
        **_fields(version, {"id", "cue", "written_to"}),
    )
    copied.save()
    for link in version.themes.all():
        models.VersionTheme.objects.create(
            version=copied,
            theme_id=link.theme_id,
            seconds=link.seconds,
            order=link.order,
        )
    return copied


# Delete ---------------------------------------------------------------------


def _delete(cue: models.Cue) -> None:
    cue.current_version = None
    cue.save()
    for version in cue.versions.all():
        version.delete()
    cue.delete()


def delete(cues: Sequence[models.Cue], *, user: User | None = None) -> None:
    """Deletes the cues and their versions in one change set.

    Undo brings them all back.
    """
    label = (
        f"Delete cue {cues[0].number}"
        if len(cues) == 1
        else f"Delete {_cue_noun(len(cues))}"
    )
    with history.recording(cues[0].project, label, user):
        for cue in cues:
            _delete(cue)
