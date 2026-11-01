"""Turns a parsed file into a NEW/UPDATE/SKIP preview.

Merge rules live here once, for every importer:

- Key = cue number, case-insensitive and trimmed. An existing active cue
  is updated (timecodes always; title only when asked); a new number
  becomes a new cue in the picture whose current IN/OUT contains its IN.
- Rows whose number doesn't match the project's pattern are excluded
  (by default) and listed.
- The same number twice in a file merges to the earliest IN, latest OUT.
- A file at a different frame rate is refused.
- Imports never delete; cues missing from the file are listed so the user
  can choose what to do with them.
"""

from collections.abc import Iterable
import dataclasses
import enum
from typing import cast

from cues import models
from cues import numbering
from cues import timecode
from cues.importers import base
from cues.services import progress


class Action(enum.StrEnum):
    """What applying a change would do."""

    NEW = "new"
    UPDATE = "update"
    SKIP = "skip"


class Mapping(enum.StrEnum):
    """How a DAW label becomes cue number and title."""

    NUMBER_AND_TITLE = "number_and_title"  # '1m01 Main Title'
    NUMBER_ONLY = "number_only"  # titles left alone
    TITLE_ONLY = "title_only"  # numbered from a start number


class RateMismatchError(base.ImportFormatError):
    """The file's frame rate isn't the project's."""


@dataclasses.dataclass(frozen=True)
class Options:
    """Choices from the import dialog.

    Attributes:
        mapping: How labels map to number and title.
        update_titles: Also update titles of existing cues.
        start_number: First number for Mapping.TITLE_ONLY.
        picture_id: Put new cues here instead of finding the picture
            whose IN/OUT contains them.
    """

    mapping: Mapping = Mapping.NUMBER_AND_TITLE
    update_titles: bool = False
    start_number: str = "1m01"
    picture_id: int | None = None


@dataclasses.dataclass(frozen=True)
class Change:
    """One row of the preview.

    Attributes:
        action: NEW, UPDATE or SKIP.
        row: The (merged) incoming row.
        reason: Why a row is skipped.
        cue_id: The existing cue, for UPDATE and SKIP.
        old_in: The existing cue's IN, for comparison.
        old_out: The existing cue's OUT.
        old_title: The existing cue's title.
        new_title: For updates, the title to set, if it changes.
        picture_id: Where a NEW cue goes; None means the placeholder
            "N/A" picture. For an UPDATE, the reel the cue moves to when
            its new IN lies in another reel; None when it stays.
        conform: For updates to cues written to older picture, how the
            new timecodes compare with the current version.
    """

    action: Action
    row: base.ImportRow
    reason: str = ""
    cue_id: int | None = None
    old_in: int | None = None
    old_out: int | None = None
    old_title: str = ""
    new_title: str | None = None
    picture_id: int | None = None
    conform: progress.Conform | None = None


@dataclasses.dataclass(frozen=True)
class MissingCue:
    """An active project cue the file doesn't mention."""

    cue_id: int
    number: str
    title: str


@dataclasses.dataclass(frozen=True)
class Preview:
    """Everything the import dialog shows before applying."""

    source: str
    changes: list[Change]
    excluded: list[base.ImportRow]
    missing: list[MissingCue]
    warnings: list[str]

    def count(self, action: Action) -> int:
        """How many changes have this action."""
        return sum(1 for change in self.changes if change.action == action)


def _check_rate(project: models.Project, parsed: base.ParsedFile) -> None:
    if parsed.frame_rate is None:
        return
    file_df = bool(parsed.drop_frame)
    if (parsed.frame_rate, file_df) != (project.frame_rate, project.drop_frame):
        stated = parsed.frame_rate + (" DF" if file_df else "")
        raise RateMismatchError(
            f"This file is at {stated}, but the project is at "
            f"{project.rate.label}. Re-export the session at the "
            "project's frame rate."
        )


def _map_rows(
    rows: Iterable[base.ImportRow], options: Options
) -> list[base.ImportRow]:
    if options.mapping == Mapping.NUMBER_AND_TITLE:
        return list(rows)
    if options.mapping == Mapping.NUMBER_ONLY:
        return [dataclasses.replace(row, title="") for row in rows]
    # Title only: number the rows in timeline order.
    ordered = sorted(
        rows, key=lambda r: (r.tc_in is None, r.tc_in or 0, r.line)
    )
    mapped = []
    number = options.start_number
    for row in ordered:
        mapped.append(
            dataclasses.replace(row, number=number, title=row.label.strip())
        )
        number = numbering.next_number(number)
    return mapped


def _gaps(rows: list[base.ImportRow], rate: timecode.Rate) -> list[str]:
    """Warnings for numbers whose clips leave silence between them.

    Clips sharing a number become one cue (a cue cut into several clips
    is common), but a gap of a second or more may mean two separate uses
    given one number by mistake; merged, the silence counts as music.
    """
    by_number: dict[str, list[base.ImportRow]] = {}
    count: dict[str, int] = {}
    for row in rows:
        key = row.number.strip().lower()
        count[key] = count.get(key, 0) + 1
        if row.tc_in is not None and row.tc_out is not None:
            by_number.setdefault(key, []).append(row)
    found = []
    for key, clips in by_number.items():
        if len(clips) < 2:
            continue
        clips.sort(key=lambda row: cast(int, row.tc_in))
        end = cast(int, clips[0].tc_out)
        widest = 0
        for clip in clips[1:]:
            widest = max(widest, cast(int, clip.tc_in) - end)
            end = max(end, cast(int, clip.tc_out))
        if widest >= rate.nominal:
            found.append(
                f"{clips[0].number.strip()}: {count[key]} clips merged into "
                f"one cue with {timecode.format_duration(widest, rate)} of "
                "silence between them. If they're separate uses, give them "
                "different numbers."
            )
    return found


def _merge_duplicates(rows: list[base.ImportRow]) -> list[base.ImportRow]:
    """Same number twice: earliest IN, latest OUT, first title."""
    merged: dict[str, base.ImportRow] = {}
    for row in rows:
        key = row.number.strip().lower()
        seen = merged.get(key)
        if seen is None:
            merged[key] = dataclasses.replace(row, number=row.number.strip())
            continue
        ins = [t for t in (seen.tc_in, row.tc_in) if t is not None]
        outs = [t for t in (seen.tc_out, row.tc_out) if t is not None]
        merged[key] = dataclasses.replace(
            seen,
            tc_in=min(ins) if ins else None,
            tc_out=max(outs) if outs else None,
            title=seen.title or row.title,
            seq_start=seen.seq_start
            if seen.seq_start is not None
            else row.seq_start,
        )
    return list(merged.values())


def picture_ranges(
    project: models.Project,
) -> list[tuple[int, int | None, int | None]]:
    """(picture id, current IN, current OUT) for each picture."""
    current = progress.current_picture_versions(project)
    versions = models.PictureVersion.objects.in_bulk(list(current.values()))
    return [
        (picture_id, versions[pv].tc_in, versions[pv].tc_out)
        for picture_id, pv in current.items()
    ]


def _picture_for(
    tc_in: int | None,
    ranges: list[tuple[int, int | None, int | None]],
    options: Options,
) -> int | None:
    if options.picture_id is not None:
        return options.picture_id
    if tc_in is None:
        return None
    for picture_id, start, end in ranges:
        if start is not None and end is not None and start <= tc_in <= end:
            return picture_id
    return None


def _update(
    row: base.ImportRow,
    cue: models.Cue,
    current_versions: dict[int, int],
    ranges: list[tuple[int, int | None, int | None]],
    options: Options,
) -> Change:
    target = (
        _picture_for(row.tc_in, ranges, options)
        if row.tc_in is not None and row.tc_in != cue.tc_in
        else None
    )
    moves_to = target if target not in (None, cue.picture_id) else None
    timing_changed = any(
        new is not None and new != old
        for new, old in (
            (row.tc_in, cue.tc_in),
            (row.tc_out, cue.tc_out),
            (row.seq_start, cue.seq_start),
        )
    )
    title_changed = (
        options.update_titles and bool(row.title) and row.title != cue.title
    )
    action = Action.UPDATE if timing_changed or title_changed else Action.SKIP
    conform = None
    version = cue.current_version
    if action == Action.UPDATE and version is not None:
        result = progress.conform(
            written_to_id=version.written_to_id,
            current_picture_version_id=current_versions.get(cue.picture_id),
            cue_in=row.tc_in if row.tc_in is not None else cue.tc_in,
            cue_out=row.tc_out if row.tc_out is not None else cue.tc_out,
            version_in=version.tc_in,
            version_out=version.tc_out,
        )
        conform = result if result.needs_conform else None
    return Change(
        action,
        row=row,
        reason="" if action == Action.UPDATE else "unchanged",
        cue_id=cue.pk,
        old_in=cue.tc_in,
        old_out=cue.tc_out,
        old_title=cue.title,
        new_title=row.title if title_changed else None,
        picture_id=moves_to if action == Action.UPDATE else None,
        conform=conform,
    )


def plan(
    project: models.Project, parsed: base.ParsedFile, options: Options
) -> Preview:
    """Works out what importing `parsed` into `project` would change.

    Reads only; nothing is written until the preview is applied.

    Args:
        project: The project to import into.
        parsed: An importer's output.
        options: Choices from the import dialog.

    Returns:
        The preview.

    Raises:
        RateMismatchError: The file states a different frame rate.
        ValueError: Mapping.TITLE_ONLY with a start number that has no
            digits.
    """
    _check_rate(project, parsed)
    rows = _map_rows(parsed.rows, options)
    existing = {
        cue.number.strip().lower(): cue
        for cue in project.cues.filter(active=True).select_related(
            "current_version"
        )
    }
    # A row for a cue the project has updates it whatever its number; a
    # new cue's number must fit Valid Cue Numbers, as when typed.
    excluded = []
    kept = []
    for row in rows:
        if row.number.strip().lower() in existing or numbering.is_valid(
            row.number, project.cue_number_pattern
        ):
            kept.append(row)
        else:
            excluded.append(row)
    numbered = [row for row in kept if row.number.strip()]
    gaps = _gaps(numbered, project.rate)
    rows = _merge_duplicates(numbered)

    ranges = picture_ranges(project)
    current_versions = progress.current_picture_versions(project)
    changes = []
    for row in rows:
        cue = existing.get(row.number.lower())
        if cue is None:
            changes.append(
                Change(
                    Action.NEW,
                    row=row,
                    picture_id=_picture_for(row.tc_in, ranges, options),
                )
            )
        else:
            changes.append(_update(row, cue, current_versions, ranges, options))

    incoming = {row.number.lower() for row in rows}
    missing = [
        MissingCue(cue.pk, cue.number, cue.title)
        for key, cue in sorted(existing.items())
        if key not in incoming
    ]
    return Preview(
        parsed.source, changes, excluded, missing, [*parsed.warnings, *gaps]
    )
