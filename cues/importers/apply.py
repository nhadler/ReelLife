"""Applies an import preview in one undoable change set.

New cues get the spreadsheet's extra fields where given (status, notes,
spotting notes, usage, cue type, version); updates change only timecodes
and, when asked, titles. Missing cues are left alone, marked Omit or made
inactive, as the user chose. The whole import is one transaction and one
change set, so `history.undo` reverts it in one step.
"""

from collections.abc import Collection
import dataclasses
import decimal
import enum
import string

from django.core.exceptions import ValidationError
from django.db.models import TextChoices
from django.db.models.functions import Lower

from accounts import audit
from accounts.models import AuditAction
from accounts.models import User
from cues import model_base
from cues import models
from cues.importers import merge
from cues.services import history
from cues.services import progress
from cues.services import projects
from cues.services import shares


class MissingAction(enum.StrEnum):
    """What to do with active cues the file doesn't mention."""

    LEAVE = "leave"
    OMIT = "omit"
    INACTIVE = "inactive"


@dataclasses.dataclass(frozen=True)
class Result:
    """What an import did."""

    change_set: models.ChangeSet
    created: int
    updated: int
    missing_changed: int
    warnings: list[str]


def _match(value: str, choices: type[TextChoices]) -> str | None:
    wanted = value.strip().casefold()
    for code, label in choices.choices:
        if wanted in (str(code).casefold(), str(label).casefold()):
            return str(code)
    return None


# Spreadsheet columns that hold a choice: (field, its choices, default).
_CHOICES: tuple[tuple[str, type[TextChoices], str], ...] = (
    ("cue_type", models.Cue.CueType, models.Cue.CueType.SCORE),
    ("usage", models.Cue.Usage, models.Cue.Usage.BI),
    ("priority", models.Priority, ""),
    ("difficulty", models.Difficulty, ""),
)


# A cue or picture the preview found was deleted before the import ran.
_GONE = (
    "Something in this project changed since the preview. Preview the file "
    "again."
)


class _Creator:
    """Builds new cues for a project, looking things up once."""

    def __init__(self, project: models.Project, warnings: list[str]) -> None:
        self.project = project
        self.warnings = warnings
        self.statuses = {s.label.casefold(): s for s in project.statuses.all()}
        self.default_status = projects.default_status(project)
        # Looked up once, not per cue: a big import ran a dozen queries a
        # cue on these alone.
        self.pictures = {p.pk: p for p in project.pictures.all()}
        current = progress.current_picture_versions(project)
        loaded = models.PictureVersion.objects.in_bulk(current.values())
        self.current_versions = {
            picture_id: loaded[pk] for picture_id, pk in current.items()
        }
        self.default_shares = shares.default_shares(project)
        self._placeholder: models.Picture | None = None

    def picture(self, picture_id: int | None) -> models.Picture:
        if picture_id is not None:
            if picture_id not in self.pictures:
                raise ValidationError(_GONE)
            return self.pictures[picture_id]
        if self._placeholder is None:
            self._placeholder = projects.placeholder_picture(self.project)
        return self._placeholder

    def status(self, change: merge.Change) -> models.StatusOption | None:
        label = change.row.fields.get("status", "")
        if not label:
            return self.default_status
        status = self.statuses.get(label.strip().casefold())
        if status is None:
            self.warnings.append(
                f"{change.row.number}: unknown status {label!r}; used "
                f"{self.default_status}."
            )
            return self.default_status
        return status

    def choice(
        self,
        change: merge.Change,
        field: str,
        choices: type[TextChoices],
        default: str,
    ) -> str:
        value = change.row.fields.get(field, "")
        if not value:
            return default
        code = _match(value, choices)
        if code is None:
            self.warnings.append(
                f"{change.row.number}: unknown {field.replace('_', ' ')} "
                f"{value!r}; used {default}."
            )
            return default
        return code

    def bpm(self, change: merge.Change) -> decimal.Decimal | None:
        text = change.row.fields.get("bpm", "").strip()
        if not text:
            return None
        try:
            value = decimal.Decimal(text)
        except decimal.InvalidOperation:
            value = None
        if value is None or not value.is_finite() or not 0 < value < 10_000:
            self.warnings.append(
                f"{change.row.number}: BPM {text!r} isn't a number; left out."
            )
            return None
        return value.quantize(decimal.Decimal("0.01"))

    def create(self, change: merge.Change) -> models.Cue:
        row = change.row
        picture = self.picture(change.picture_id)
        cue = models.Cue(
            project=self.project,
            picture=picture,
            number=row.number,
            title=row.title,
            tc_in=row.tc_in,
            tc_out=row.tc_out,
            seq_start=row.seq_start,
            status=self.status(change),
            **{
                field: self.choice(change, field, choices, default)
                for field, choices, default in _CHOICES
            },
            scene=row.fields.get("scene", ""),
            artist=row.fields.get("artist", ""),
            cue_sheet_title=row.fields.get("cue_sheet_title", ""),
        )
        # The project, picture and status were loaded in this transaction,
        # and apply() checked the numbers for the whole import.
        model_base.full_clean(
            cue,
            trusted=("project", "picture", "status"),
            checked=(NUMBER_UNIQUE,),
        )
        cue.save()
        written_to = self.current_versions.get(picture.pk)
        if written_to is not None:
            written_to.picture = picture  # spares clean() a query
        # Every cue has a version; it holds the notes.
        version = models.CueVersion(
            cue=cue,
            label=row.fields.get("version") or "v1",
            written_to=written_to,
            tc_in=cue.tc_in,
            tc_out=cue.tc_out,
            seq_start=cue.seq_start,
            bpm=self.bpm(change),
            **{
                name: row.fields.get(name, "")
                for name in models.VERSION_NOTE_FIELDS
                if name != "bpm"
            },
        )
        # A new cue has no other versions, so the label is free.
        model_base.full_clean(
            version,
            trusted=("cue", "written_to"),
            checked=("cue_version_label_unique",),
        )
        version.save()
        cue.current_version = version
        cue.save(update_fields=["current_version", "updated_at"])
        shares.give_defaults(cue, self.default_shares)
        return cue


def field_warnings(
    project: models.Project, changes: Collection[merge.Change]
) -> list[str]:
    """What applying would substitute or leave out in new cues' fields.

    The same checks `apply` makes (unknown statuses and choices fall back
    to the default, a BPM that isn't a number is left out), run on the
    preview so the user sees them before confirming.
    """
    warnings: list[str] = []
    creator = _Creator(project, warnings)
    for change in changes:
        if change.action != merge.Action.NEW:
            continue
        creator.status(change)
        for field, choices, default in _CHOICES:
            creator.choice(change, field, choices, default)
        creator.bpm(change)
    return warnings


NUMBER_UNIQUE = "cue_number_unique_among_active"
# SQLite's LOWER(), which the constraint uses, folds ASCII letters only.
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def _check_numbers(
    project: models.Project, new: Collection[merge.Change]
) -> None:
    """Checks new cues' numbers are free, in one query for the import.

    Raises:
        ValidationError: Naming the first number an active cue has.
    """
    taken = set(
        project.cues.filter(active=True)
        .annotate(key=Lower("number"))
        .values_list("key", flat=True)
    )
    for change in new:
        key = change.row.number.translate(_ASCII_LOWER)
        if key in taken:
            raise ValidationError(
                f"{change.row.number}: Another active cue already has this "
                "number."
            )
        taken.add(key)


def _update(
    project: models.Project, cues: dict[int, models.Cue], change: merge.Change
) -> None:
    if change.cue_id is None:
        raise ValueError("an update needs the existing cue's id")
    cue = cues.get(change.cue_id)
    if cue is None:
        raise ValidationError(_GONE)
    cue.project = project
    before = model_base.snapshot(cue)
    row = change.row
    for field in ("tc_in", "tc_out", "seq_start"):
        value = getattr(row, field)
        if value is not None:
            setattr(cue, field, value)
    if change.new_title is not None:
        cue.title = change.new_title
    if change.picture_id is not None:
        cue.picture_id = change.picture_id  # its new IN is in that reel
    model_base.full_clean(cue, before)
    cue.save()


def _handle_missing(
    project: models.Project,
    preview: merge.Preview,
    action: MissingAction,
    only: Collection[int] | None,
) -> int:
    if action == MissingAction.LEAVE:
        return 0
    ids = [
        m.cue_id for m in preview.missing if only is None or m.cue_id in only
    ]
    omit = project.statuses.filter(kind=models.StatusKind.OMIT).first()
    changed = 0
    for cue in project.cues.filter(pk__in=ids, active=True):
        if action == MissingAction.INACTIVE:
            cue.active = False
        elif omit is not None:
            cue.status = omit
        else:
            continue
        cue.save()
        changed += 1
    return changed


def apply(
    project: models.Project,
    preview: merge.Preview,
    *,
    user: User | None = None,
    filename: str = "",
    include: Collection[int] | None = None,
    missing_action: MissingAction = MissingAction.LEAVE,
    missing_ids: Collection[int] | None = None,
) -> Result:
    """Writes a previewed import.

    Args:
        project: The project the preview was planned against.
        preview: From merge.plan, planned in this request.
        user: Who is importing (for history and the audit log).
        filename: The uploaded file's name, for the change set label.
        include: Indexes into preview.changes to apply; None means all
            NEW and UPDATE changes. SKIP changes are never applied.
        missing_action: What to do with cues missing from the file.
        missing_ids: Limit missing_action to these cue ids.

    Returns:
        Counts, warnings and the change set (pass it to history.undo).

    Raises:
        django.core.exceptions.ValidationError: A cue would be invalid;
            nothing is written.
    """
    warnings: list[str] = []
    label = (
        f"Import {filename}".strip()
        if filename
        else f"Import ({preview.source})"
    )
    created = updated = 0
    with history.recording(project, label, user) as change_set:
        creator = _Creator(project, warnings)
        chosen = [
            change
            for index, change in enumerate(preview.changes)
            if include is None or index in include
        ]
        existing = project.cues.select_related(
            "picture", "status", "current_version"
        ).in_bulk([c.cue_id for c in chosen if c.action == merge.Action.UPDATE])
        _check_numbers(
            project, [c for c in chosen if c.action == merge.Action.NEW]
        )
        for change in chosen:
            if change.action == merge.Action.NEW:
                creator.create(change)
                created += 1
            elif change.action == merge.Action.UPDATE:
                _update(project, existing, change)
                updated += 1
        missing = _handle_missing(project, preview, missing_action, missing_ids)
    audit.record(
        AuditAction.IMPORT,
        user=user,
        project=project.pk,
        source=preview.source,
        filename=filename,
        created=created,
        updated=updated,
        missing_changed=missing,
    )
    return Result(change_set, created, updated, missing, warnings)
