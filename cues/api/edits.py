"""Applies one field edit from the grid or the cue details page.

Grid columns: cue columns edit the
cue; version columns edit its current version, creating v1 first if it
has none. The details page also edits the cue's picture and
active flag, and any version's fields. Every edit is validated with the
model's rules, recorded as a change set (so it can be undone), and bumps
the cue's updated_at, which both screens use to detect concurrent edits.
"""

import decimal
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import prefetch_related_objects

from accounts.models import User
from cues import columns
from cues import model_base
from cues import models
from cues import numbering
from cues import timecode
from cues.api import grid
from cues.services import authoring
from cues.services import history


class EditError(ValueError):
    """The edit can't be applied; the message is for the user."""


_TEXT = {
    "number",
    "title",
    "scene",
    "artist",
    "cue_sheet_title",
    "cue_sheet_notes",
    *models.DEPARTMENT_TEXT_FIELDS,
}
# Version text fields (models.VERSION_NOTE_FIELDS but BPM).
_VERSION_TEXT = {
    "in_note",
    "out_note",
    "spotting_notes",
    "temp",
    "scene_description",
    "key",
    "meter",
    "notes",
    "revision_notes",
    "change_notes",
    "file_link",
}
_CUE_TIMECODES = {"tc_in", "tc_out", "seq_start"}
_VERSION_TIMECODES = {
    "version_in": "tc_in",
    "version_out": "tc_out",
    "version_seq_start": "seq_start",
}
_CHOICE_FIELDS = {
    "usage",
    "usage_theme",
    "priority",
    "difficulty",
    "cue_type",
}
# Cue fields only the details page edits, with their change set labels.
DETAIL_ONLY = {"picture": "Picture", "active": "Active", "lane": "Lane"}
# Every version field the details page edits, with its label.
VERSION_FIELDS = {
    "label": "Version",
    "written_to": "Written To",
    "tc_in": "IN",
    "tc_out": "OUT",
    "seq_start": "Seq. Start",
    "change_notes": "Conform Notes",
    "review_status": "Review Status",
    "sub_status": "Sub Status",
    "file_link": "File Link",
    "delivered_at": "Delivered",
    "in_note": "IN Note",
    "out_note": "OUT Note",
    "spotting_notes": "Spotting Notes",
    "temp": "Temp",
    "scene_description": "Scene Description",
    "key": "Key",
    "bpm": "BPM",
    "meter": "Meter",
    "notes": "Notes",
    "revision_notes": "Revision Notes",
    "themes": "Theme",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise EditError("Expected text.")
    return value.strip()


def _frames(value: Any, rate: timecode.Rate) -> int | None:
    text = _text(value)
    if not text:
        return None
    try:
        return timecode.parse(text, rate)
    except timecode.TimecodeError as exc:
        raise EditError(str(exc)) from exc


def _choice(field: str, value: Any) -> str:
    text = _text(value)
    allowed = {code for code, _ in grid.CHOICES[field]}
    if text not in allowed:
        raise EditError(f"{text!r} isn't a valid choice.")
    return text


def _department_choice(cue: models.Cue, field: str, value: Any) -> str:
    """A label from the project's list for that department, or ''."""
    text = _text(value)
    if (
        text
        and not cue.project.department_statuses.filter(
            field=field, name=text
        ).exists()
    ):
        raise EditError(f"{text!r} isn't in this project's list.")
    return text


def _current_version(cue: models.Cue) -> models.CueVersion:
    """The current version, creating v1 (to current picture) if needed."""
    if cue.current_version is not None:
        cue.current_version.cue = cue  # the same object, so no reload
        return cue.current_version
    written_to = cue.picture.current_version()
    version = models.CueVersion.objects.create(
        cue=cue,
        label="v1",
        written_to=written_to,
        tc_in=cue.tc_in,
        tc_out=cue.tc_out,
        seq_start=cue.seq_start,
    )
    cue.current_version = version
    return version


def _edit_duration(cue: models.Cue, value: Any) -> None:
    """Sets OUT from IN plus a typed duration."""
    if cue.tc_in is None:
        raise EditError("Set IN before the duration.")
    text = _text(value)
    if not text:
        cue.tc_out = None
        return
    try:
        cue.tc_out = cue.tc_in + timecode.parse_duration(text, cue.project.rate)
    except timecode.TimecodeError as exc:
        raise EditError(str(exc)) from exc


def _edit_status(cue: models.Cue, value: Any) -> None:
    if value in (None, ""):
        cue.status = None
        return
    status = cue.project.statuses.filter(pk=value).first()
    if status is None:
        raise EditError("That status isn't in this project.")
    cue.status = status


def _edit_picture(cue: models.Cue, value: Any) -> None:
    picture = cue.project.pictures.filter(pk=value).first() if value else None
    if picture is None:
        raise EditError("That picture isn't in this project.")
    cue.picture = picture


def _edit_lane(cue: models.Cue, value: Any) -> None:
    """The timeline lane: 0 places the cue itself, else 1 to the project's."""
    lanes = cue.project.lanes
    if isinstance(value, bool) or not isinstance(value, int):
        raise EditError("Expected a lane number.")
    if not 0 <= value <= lanes:
        raise EditError(f"Lanes run from 1 to {lanes}.")
    cue.lane = value


def _edit_bpm(version: models.CueVersion, value: Any) -> None:
    text = _text(value)
    try:
        version.bpm = decimal.Decimal(text) if text else None
    except decimal.InvalidOperation as exc:
        raise EditError("BPM must be a number.") from exc


def _edit_cue(cue: models.Cue, field: str, value: Any) -> None:
    if field in _TEXT:
        text = _text(value)
        if field == "number" and not text:
            raise EditError("A cue needs a number.")
        if field == "number" and text != cue.number:
            problem = numbering.misfit(text, cue.project.cue_number_pattern)
            if problem:
                raise EditError(problem)
        setattr(cue, field, text)
    elif field in _CUE_TIMECODES:
        setattr(cue, field, _frames(value, cue.project.rate))
    elif field == "duration":
        _edit_duration(cue, value)
    elif field == "status":
        _edit_status(cue, value)
    elif field == "picture":
        _edit_picture(cue, value)
    elif field == "lane":
        _edit_lane(cue, value)
    elif field == "instrumentation":
        _set_chips(
            cue.instrumentation,
            cue.project.instruments,
            "instrument",
            value,
            "instrument",
        )
    elif field in _CHOICE_FIELDS:
        setattr(cue, field, _choice(field, value))
    elif field in models.DEPARTMENT_STATUS_FIELDS:
        setattr(cue, field, _department_choice(cue, field, value))
    else:  # the flags
        if not isinstance(value, bool):
            raise EditError("Expected true or false.")
        setattr(cue, field, value)


# The default colour of a sub status, theme or instrument typed into the
# grid (grey).
NEW_SUB_STATUS_COLOR = "#6b7280"
# A chip list (themes, instrumentation) holds at most this many entries.
MAX_CHIPS = 40


def _option_named(manager: Any, value: Any, what: str) -> Any:
    """A list entry by id, or by name, adding a new name to the list."""
    if isinstance(value, int) and not isinstance(value, bool):
        option = manager.filter(pk=value).first()
        if option is None:
            raise EditError(f"That {what} isn't in this project.")
        return option
    name = _text(value)
    found = manager.filter(name__iexact=name).first()
    if found is not None:
        return found
    option = manager.model(
        project=manager.instance,
        name=name,
        color=NEW_SUB_STATUS_COLOR,
        order=manager.count(),
    )
    try:
        option.full_clean()
    except ValidationError as exc:
        raise EditError(_messages(exc)) from exc
    option.save()
    return option


def _chips(value: Any) -> list[tuple[Any, int | None]]:
    """Parses a chip list: [{"id" or "name", "seconds"?}, ...]."""
    if not isinstance(value, list) or len(value) > MAX_CHIPS:
        raise EditError(f"Expected a list of at most {MAX_CHIPS} entries.")
    parsed = []
    for chip in value:
        if not isinstance(chip, dict) or not ("id" in chip or "name" in chip):
            raise EditError("Each entry needs an id or a name.")
        seconds = chip.get("seconds")
        if seconds is not None and (
            isinstance(seconds, bool)
            or not isinstance(seconds, int)
            or not 0 <= seconds <= 86_400
        ):
            raise EditError("A duration is a number of seconds.")
        parsed.append((chip["id"] if "id" in chip else chip["name"], seconds))
    return parsed


def _set_chips(
    links: Any, options: Any, link_field: str, value: Any, what: str
) -> None:
    """Replaces a chip list (an owner's link rows) with `value`, in order.

    Kept entries are updated in place, so their history shows the change.
    """
    wanted: list[tuple[Any, int | None]] = []
    for key, seconds in _chips(value):
        option = _option_named(options, key, what)
        if option.pk not in [o.pk for o, _ in wanted]:
            wanted.append((option, seconds))
    existing = {getattr(link, f"{link_field}_id"): link for link in links.all()}
    for order, (option, seconds) in enumerate(wanted):
        link = existing.pop(option.pk, None)
        if link is None:
            links.create(**{link_field: option}, seconds=seconds, order=order)
        elif (link.seconds, link.order) != (seconds, order):
            link.seconds, link.order = seconds, order
            link.save()
    for link in existing.values():
        link.delete()


def _sub_status(
    project: models.Project, value: Any
) -> models.SubStatusOption | None:
    """An option by id, none for blank, or one named by new text.

    Typing a name that isn't on the list adds it (Oct 31),
    inside the edit's change set: undoing the edit removes the new
    option again, unless another version has taken it up since.
    """
    if value in (None, ""):
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        option = project.sub_statuses.filter(pk=value).first()
        if option is None:
            raise EditError("That sub status isn't in this project.")
        return option
    label = _text(value)
    found = project.sub_statuses.filter(label__iexact=label).first()
    if found is not None:
        return found
    option = models.SubStatusOption(
        project=project,
        label=label,
        color=NEW_SUB_STATUS_COLOR,
        order=project.sub_statuses.count(),
    )
    try:
        option.full_clean()
    except ValidationError as exc:
        raise EditError(_messages(exc)) from exc
    option.save()
    return option


def _edit_written_to(version: models.CueVersion, value: Any) -> None:
    if value in (None, ""):
        version.written_to = None
        return
    target = models.PictureVersion.objects.filter(
        pk=value, picture__project=version.cue.project
    ).first()
    if target is None:
        raise EditError("That picture version isn't in this project.")
    version.written_to = target


def _edit_delivered_at(version: models.CueVersion, value: Any) -> None:
    text = _text(value)
    try:
        version.delivered_at = forms.DateTimeField(required=False).clean(text)
    except ValidationError as exc:
        raise EditError("Enter a date and time.") from exc


def _set_version_field(
    version: models.CueVersion, field: str, value: Any
) -> None:
    """Sets one version field (model field names) from an edit value."""
    before = model_base.snapshot(version)
    if field in ("tc_in", "tc_out", "seq_start"):
        setattr(version, field, _frames(value, version.cue.project.rate))
    elif field == "label":
        text = _text(value)
        if not text:
            raise EditError("A version needs a label.")
        version.label = text
    elif field in _VERSION_TEXT:
        setattr(version, field, _text(value))
    elif field == "bpm":
        _edit_bpm(version, value)
    elif field == "review_status":
        version.review_status = _choice("review_status", value)
    elif field == "written_to":
        _edit_written_to(version, value)
    elif field == "sub_status":
        version.sub_status = _sub_status(version.cue.project, value)
    elif field == "themes":
        project = version.cue.project
        _set_chips(version.themes, project.themes, "theme", value, "theme")
    else:  # delivered_at
        _edit_delivered_at(version, value)
    model_base.full_clean(version, before)
    version.save()


def _edit_version(cue: models.Cue, field: str, value: Any) -> None:
    name = _VERSION_TIMECODES.get(field, field)
    version = _current_version(cue)
    _set_version_field(version, name, value)
    if name == "review_status":
        authoring.sync_review_status(cue, version)


def _messages(error: ValidationError) -> str:
    """Every message, field errors and constraint violations alike."""
    return " ".join(error.messages)


# One value for several cues makes no sense for these.
NOT_BATCH = frozenset(
    {
        "number",
        "tc_in",
        "tc_out",
        "seq_start",
        "version_in",
        "version_out",
        "version_seq_start",
    }
)


def _column(field: str) -> tuple[str, columns.Source]:
    """A field's name for change set labels, and where it's stored.

    Raises:
        EditError: Unknown or read-only column.
    """
    if field in DETAIL_ONLY:
        return DETAIL_ONLY[field], columns.Source.CUE
    if field in grid.EDITABLE:
        column = columns.BY_ID[field]
        return column.label, column.source
    raise EditError(f"{field} can't be edited here.")


def _apply_one(
    cue: models.Cue, field: str, value: Any, source: columns.Source
) -> None:
    """The edit itself, inside the caller's change set."""
    before = model_base.snapshot(cue)
    if source == columns.Source.VERSION:
        _edit_version(cue, field, value)
    else:
        _edit_cue(cue, field, value)
    model_base.full_clean(cue, before)
    cue.save()  # also bumps updated_at for version edits


def apply(
    cue: models.Cue, field: str, value: Any, user: User | None = None
) -> models.Cue:
    """Applies `value` to `field` of `cue` as one undoable change.

    Args:
        cue: The cue being edited.
        field: A column id (cues/columns.py).
        value: The new value as the grid sends it: text for text and
            timecode columns, a code or id for selects, a bool for flags.
        user: Who is editing.

    Returns:
        The saved cue.

    Raises:
        EditError: Unknown or read-only column, or an invalid value. The
            database is unchanged.
    """
    name, source = _column(field)
    try:
        with history.recording(cue.project, f"Edit {cue.number} {name}", user):
            _apply_one(cue, field, value, source)
    except ValidationError as exc:
        raise EditError(_messages(exc)) from exc
    return cue


def apply_many(
    cues: list[models.Cue], field: str, value: Any, user: User | None = None
) -> list[models.Cue]:
    """Applies one value to the same field of several cues (batch edit).

    One change set, so Undo reverts all of them.

    Args:
        cues: The selected cues, all of one project.
        field: A column id; not one in NOT_BATCH.
        value: As for apply.
        user: Who is editing.

    Returns:
        The saved cues.

    Raises:
        EditError: A field that can't be batch edited, or a value one of
            the cues refuses (naming it). The database is unchanged.
    """
    name, source = _column(field)
    if field in NOT_BATCH:
        raise EditError(f"{name} can't be set on several cues at once.")
    label = f"Edit {len(cues)} cues {name}"
    cue = cues[0]
    project = cue.project
    # Load what validation reads for every cue in a few queries, and
    # share the project, instead of a dozen queries a cue.
    for each in cues:
        each.project = project
    prefetch_related_objects(
        cues, "status", "picture", "current_version__written_to__picture"
    )
    try:
        with history.recording(project, label, user):
            for cue in cues:
                _apply_one(cue, field, value, source)
    except ValidationError as exc:
        raise EditError(f"{cue.number}: {_messages(exc)}") from exc
    except EditError as exc:
        raise EditError(f"{cue.number}: {exc}") from exc
    return cues


def apply_version(
    version: models.CueVersion, field: str, value: Any, user: User | None = None
) -> models.CueVersion:
    """Applies `value` to `field` of any version, as one undoable change.

    Args:
        version: The version being edited (current or not).
        field: A key of VERSION_FIELDS (model field names).
        value: Text for text, timecode and date fields; a code or id for
            selects.
        user: Who is editing.

    Returns:
        The saved version.

    Raises:
        EditError: Unknown field or an invalid value. The database is
            unchanged.
    """
    if field not in VERSION_FIELDS:
        raise EditError(f"{field} can't be edited here.")
    cue = version.cue
    label = f"Edit {cue.number} {version.label} {VERSION_FIELDS[field]}"
    try:
        with history.recording(cue.project, label, user):
            _set_version_field(version, field, value)
            if field == "review_status":
                authoring.sync_review_status(cue, version)
            cue.save()  # bumps updated_at; may carry a synced status
    except ValidationError as exc:
        raise EditError(_messages(exc)) from exc
    return version
