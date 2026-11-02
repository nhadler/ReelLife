"""The canonical PRO cue sheet.

Built from a project and exported in two formats (RapidCue XLSX,
generic CSV). Durations are timecode time rounded to the nearest second,
halves up, as the grid shows them. The checklist flags missing or
inconsistent data before export.
"""

import dataclasses
import datetime
from decimal import Decimal
import fractions

from cues import models
from cues import timecode
from cues.services import conform
from cues.services import progress
from cues.services import shares as share_rules

# The usages RapidCue accepts; a cue's theme (MT, ET, Logo) wins over its
# background or visual usage.
RAPIDCUE_USAGES = ("BI", "BV", "VI", "VV", "MT", "ET", "Logo")
_THEME_USAGES = {"MT": "MT", "ET": "ET", "LOGO": "Logo"}


@dataclasses.dataclass(frozen=True)
class Options:
    """What the export dialog lets the composer choose."""

    revision: bool = False
    prefix_numbers: bool = False
    include_ipi: bool = True
    include_notes: bool = False
    include_inactive: bool = False
    include_artist: bool = False


@dataclasses.dataclass(frozen=True)
class Party:
    """One interested party on a cue."""

    name: str
    first_name: str
    last_name: str
    kind: str  # writer or publisher
    is_writer: bool  # a writer's role (C, A, CA, AR, AD)
    role: str  # C, A, CA, AR, AD, E, SE, AM
    society: str
    ipi: str
    share: Decimal  # percent, e.g. 50


@dataclasses.dataclass(frozen=True)
class Line:
    """One cue on the sheet."""

    sequence: int
    number: str
    title: str
    usage: str
    tc_in: int | None  # frames
    tc_out: int | None
    seconds: int  # rounded
    parties: list[Party]
    artist: str
    notes: str
    # For the checklist: the status, when the cue isn't composed yet, and
    # whether its current version was written to older picture.
    unfinished: str = ""
    older_picture: bool = False


# One attribute per header field of the sheet, as RapidCue lists them.
@dataclasses.dataclass(frozen=True)
class Header:  # pylint: disable=too-many-instance-attributes
    """The sheet's production details."""

    title: str
    aka: str
    episode_title: str
    episode_number: str
    production_number: str
    company: str
    company_address: str
    network: str
    distributor: str
    release_date: datetime.date | None
    country: str
    category: str
    version: str
    preparer: str
    preparer_email: str
    program_seconds: int
    prepared_on: datetime.date
    revision: bool


@dataclasses.dataclass(frozen=True)
class Issue:
    """Something to fix before sending; `number` names the cue, if any."""

    number: str
    message: str


@dataclasses.dataclass(frozen=True)
class CueSheet:
    """A project's cue sheet."""

    header: Header
    lines: list[Line]
    rate: timecode.Rate

    @property
    def music_seconds(self) -> int:
        """All the music, in rounded seconds."""
        return sum(line.seconds for line in self.lines)


def split_name(name: str) -> tuple[str, str]:
    """A writer's first (and middle) names and last name.

    'Casey J. Composer' -> ('Casey J.', 'Composer'); 'van der Berg, Ann'
    -> ('Ann', 'van der Berg') for last names of more than one word.
    """
    name = " ".join(name.split())
    if "," in name:
        last, first = (part.strip() for part in name.split(",", 1))
        return first, last
    first, _, last = name.rpartition(" ")
    return first, last


def _seconds(frames: int, rate: timecode.Rate) -> int:
    return int((timecode.seconds(frames, rate) + fractions.Fraction(1, 2)) // 1)


def _usage(cue: models.Cue) -> str:
    return _THEME_USAGES.get(cue.usage_theme, cue.usage)


def _notes(cue: models.Cue) -> str:
    """Source, interrupted and licensed info, for the notes option."""
    parts = []
    if cue.plays_as_source:
        parts.append("Source")
    if cue.interrupted_use:
        parts.append("Interrupted")
    if cue.status is not None and cue.status.kind == models.StatusKind.LICENSE:
        parts.append("Licensed")
    if cue.cue_sheet_notes.strip():
        parts.append(cue.cue_sheet_notes.strip())
    return "; ".join(parts)


def _party(share: models.CueShare, include_ipi: bool) -> Party:
    party = share.party
    # Writers carry first and last names; older rows fall back to a split.
    first, last = (
        (party.first_name, party.last_name)
        if party.last_name
        else split_name(party.name)
    )
    return Party(
        name=party.name,
        first_name=first,
        last_name=last,
        kind=party.kind,
        is_writer=share.is_writer,
        role=share.role,
        society=party.society,
        ipi=party.ipi if include_ipi else "",
        share=share.share,
    )


def included(cue: models.Cue, options: Options) -> bool:
    """Whether a cue goes on the sheet.

    Inactive cues and those Omitted or On hold don't, unless asked.
    """
    if options.include_inactive:
        return True
    return cue.active and (
        cue.status is None or cue.status.category != "inactive"
    )


def build(
    project: models.Project,
    options: Options,
    today: datetime.date | None = None,
) -> CueSheet:
    """The project's cue sheet, in picture order."""
    rate = project.rate
    stale = {item.cue.pk for item in conform.needing_conform(project)}
    cues = [
        cue
        for cue in project.cues.select_related("status", "picture")
        .prefetch_related("shares__party")
        .order_by("picture__order", "picture__name", "tc_in", "number")
        if included(cue, options)
    ]
    lines = []
    for sequence, cue in enumerate(cues, start=1):
        title = cue.cue_sheet_title or cue.title
        if options.prefix_numbers:
            title = f"{cue.number} {title}".strip()
        lines.append(
            Line(
                sequence=sequence,
                number=cue.number,
                title=title,
                usage=_usage(cue),
                tc_in=cue.tc_in,
                tc_out=cue.tc_out,
                seconds=_seconds(max(cue.duration or 0, 0), rate),
                parties=[
                    _party(share, options.include_ipi)
                    for share in cue.shares.all()
                ],
                artist=cue.artist if options.include_artist else "",
                notes=_notes(cue) if options.include_notes else "",
                unfinished=cue.status.label
                if cue.status is not None
                and cue.status.category == "unapproved"
                and not cue.status.counts_as_composed
                else "",
                older_picture=cue.pk in stale,
            )
        )
    episode = project.episode_number
    header = Header(
        title=project.title,
        aka=project.aka,
        episode_title=project.episode_title,
        episode_number="" if episode is None else str(episode),
        production_number=project.production_number,
        company=project.production_company,
        company_address=project.company_address,
        network=project.network,
        distributor=project.distributor,
        release_date=project.release_date,
        country=project.country,
        category=project.cue_sheet_category,
        version=project.cue_sheet_version,
        preparer=project.preparer,
        preparer_email=project.preparer_email,
        program_seconds=_seconds(progress.runtime(project), rate),
        prepared_on=today or datetime.date.today(),
        revision=options.revision,
    )
    return CueSheet(header=header, lines=lines, rate=rate)


def checklist(sheet: CueSheet) -> list[Issue]:
    """What to fix before sending."""
    header = sheet.header
    issues = [
        Issue("", f"Set the {what} in Project Settings, Cue Sheet.")
        for what, value in (
            ("production company", header.company),
            ("category", header.category),
            ("preparer", header.preparer),
        )
        if not value
    ]
    if not header.program_seconds:
        issues.append(
            Issue("", "The program has no length: log the picture's reels.")
        )
    if not sheet.lines:
        issues.append(Issue("", "No cues to list."))
    for line in sheet.lines:
        issues.extend(Issue(line.number, text) for text in _line_issues(line))
    return issues


def _line_issues(line: Line) -> list[str]:
    found = []
    if line.unfinished:
        found.append(
            f"Still {line.unfinished}: is it in the final picture as written?"
        )
    if line.older_picture:
        found.append(
            "Written to older picture: conform it, or check its timings "
            "against the final cut."
        )
    if not line.title:
        found.append("No title.")
    if not line.seconds:
        found.append("No length: set IN and OUT.")
    if line.usage not in RAPIDCUE_USAGES:
        found.append(
            f"Usage {line.usage or '(none)'} isn't one RapidCue takes "
            "(BI, BV, VI, VV, MT, ET, Logo)."
        )
    found.extend(
        share_rules.problems(
            share_rules.Line(p, p.role, p.share) for p in line.parties
        )
        or ([] if line.parties else ["Add at least one writer."])
    )
    found.extend(f"{p.name} has no PRO." for p in line.parties if not p.society)
    return found
