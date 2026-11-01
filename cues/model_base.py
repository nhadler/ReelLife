"""Building blocks shared by the cues models (cues.models, cues.lists)."""

from collections.abc import Collection, Mapping
from typing import Any, cast

from django.core import validators
from django.core.exceptions import NON_FIELD_ERRORS
from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS
from django.db import models
from django.db.models import F
from django.db.models import Q
from simple_history.models import HistoricalRecords

from cues import timecode

_HEX_COLOR = validators.RegexValidator(
    r"^#[0-9a-fA-F]{6}$", "Enter a colour like #1a2b3c."
)
_FRAMES = [validators.MinValueValidator(0)]


class Timestamped(models.Model):
    """Adds created_at and updated_at (the latter also guards edits)."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ChangeSetLink(models.Model):
    """Ties a history row to the change set that wrote it (for undo).

    Every tracked model's history uses this base, so one import or tool
    run can be found across models and undone in one step
    (cues.services.history).
    """

    change_set = models.ForeignKey(
        "cues.ChangeSet",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # Order of writes within the change set; timestamps can tie.
    change_seq = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        abstract = True


def _history(**kwargs: object) -> HistoricalRecords:
    return HistoricalRecords(bases=[ChangeSetLink], **kwargs)


def _past_the_end(
    instance: models.Model, rate: timecode.Rate, *fields: str
) -> dict[str, str]:
    """Errors for timecode fields past 99:59:59:FF, which nothing can show.

    The database only checks they aren't negative; a later value (say IN
    near the end plus a long typed duration) would break every page that
    formats it.
    """
    last = timecode.max_frames(rate)
    message = f"Timecodes end at {timecode.format_timecode(last, rate)}."
    return {
        name: message
        for name in fields
        if (getattr(instance, name) or 0) > last
    }


def _truth(q: Q, values: Mapping[str, Any]) -> bool | None:
    """A condition's SQL truth for one row: True, False or None (unknown).

    Understands what FrameCheck conditions use: isnull, and gte against a
    value or another field.

    Raises:
        NotImplementedError: Any other lookup.
    """
    results: list[bool | None] = []
    for child in cast(list[Any], q.children):
        if isinstance(child, Q):
            results.append(_truth(child, values))
            continue
        lookup, wanted = child
        name, _, op = lookup.partition("__")
        value = values[name]
        if isinstance(wanted, F):
            wanted = values[_named(wanted)]
        if op == "isnull":
            results.append((value is None) == wanted)
        elif op == "gte":
            results.append(
                None if value is None or wanted is None else value >= wanted
            )
        else:
            raise NotImplementedError(lookup)
    if q.connector == Q.OR:
        truth = True if True in results else None if None in results else False
    else:
        truth = False if False in results else None if None in results else True
    return truth if truth is None or not q.negated else not truth


def _named(reference: F) -> str:
    """The field an F() refers to (django-stubs leave out its name)."""
    return str(cast(Any, reference).name)


def _fields(q: Q) -> set[str]:
    """The fields a condition reads."""
    names: set[str] = set()
    for child in cast(list[Any], q.children):
        if isinstance(child, Q):
            names |= _fields(child)
        else:
            lookup, wanted = child
            names.add(lookup.partition("__")[0])
            if isinstance(wanted, F):
                names.add(_named(wanted))
    return names


class FrameCheck(models.CheckConstraint):
    """A CheckConstraint that full_clean checks in memory.

    Django validates a CheckConstraint with a query each time, so an
    import of a thousand cues ran five thousand of them. These conditions
    only compare a row's own frame counts, so they're evaluated on the
    instance instead, with SQL's rule that unknown passes. The database
    still enforces them, and migrations see a plain CheckConstraint.
    """

    def validate(
        self,
        model: type[models.Model],
        instance: models.Model,
        exclude: set[str] | None = None,
        using: str = DEFAULT_DB_ALIAS,
    ) -> None:
        """Raises ValidationError if the instance breaks the condition."""
        condition = cast(Q, self.condition)  # always a Q here
        fields = _fields(condition)
        if exclude and fields & set(exclude):
            return
        values = {name: getattr(instance, name) for name in fields}
        try:
            truth = _truth(condition, values)
        except NotImplementedError:
            super().validate(model, instance, exclude, using)
            return
        if truth is False:
            raise ValidationError(
                self.get_violation_error_message(),
                code=self.violation_error_code,
            )

    def deconstruct(self) -> tuple[str, Any, Any]:
        """Deconstructs as CheckConstraint, so no migration changes."""
        _, args, kwargs = super().deconstruct()
        return "django.db.models.CheckConstraint", args, kwargs


def _timecode_constraints(prefix: str) -> list[models.BaseConstraint]:
    """Non-negative frames and OUT not before IN, for a model's fields."""
    return [
        FrameCheck(
            condition=Q(tc_in__isnull=True) | Q(tc_in__gte=0),
            name=f"{prefix}_tc_in_non_negative",
            violation_error_message="IN can't be negative.",
        ),
        FrameCheck(
            condition=Q(tc_in__isnull=True)
            | Q(tc_out__isnull=True)
            | Q(tc_out__gte=models.F("tc_in")),
            name=f"{prefix}_tc_out_not_before_in",
            violation_error_message="OUT can't be before IN.",
        ),
    ]


def snapshot(instance: models.Model) -> dict[str, Any]:
    """Every field's value (ids for foreign keys), to compare after an edit."""
    return {
        field.name: getattr(instance, field.attname)
        for field in instance._meta.concrete_fields
    }


def _constraint_fields(constraint: models.BaseConstraint) -> set[str] | None:
    """The fields a constraint reads, or None if that can't be told."""
    names: set[str] = set()
    if isinstance(constraint, models.UniqueConstraint):
        names.update(constraint.fields)
        for expression in constraint.expressions:
            parts = (
                [expression]
                if isinstance(expression, F)
                else cast(Any, expression).flatten()
            )
            names.update(_named(p) for p in parts if isinstance(p, F))
        if constraint.condition is not None:
            names |= _fields(constraint.condition)
        return names
    if isinstance(constraint, models.CheckConstraint) and isinstance(
        constraint.condition, Q
    ):
        return _fields(constraint.condition)
    return None


def full_clean(
    instance: models.Model,
    before: Mapping[str, Any] | None = None,
    *,
    trusted: Collection[str] = (),
    checked: Collection[str] = (),
) -> None:
    """Model.full_clean without the queries a bulk write needn't repeat.

    Django checks every foreign key and unique constraint with a query,
    which imports and batch edits repeated for each row. This skips:

    - existence checks for `trusted` foreign keys (loaded by the caller in
      this transaction) and for keys unchanged since `before`;
    - constraints whose fields are all unchanged since `before` (the
      database already holds the row to them);
    - constraints named in `checked`, which the caller checked for the
      whole batch at once.

    Unlike excluding fields from full_clean, a changed field's unique
    constraints are still checked even when they also use unchanged ones.

    Args:
        instance: The row about to be saved.
        before: snapshot(instance) from before the edit; None for a new row.
        trusted: Foreign key names whose rows are known to exist.
        checked: Constraint names the caller has already checked.

    Raises:
        ValidationError: As full_clean.
    """
    now = snapshot(instance)
    same = (
        {name for name in now if name in before and before[name] == now[name]}
        if before is not None
        else set()
    )
    relations = {
        field.name
        for field in instance._meta.concrete_fields
        if field.is_relation
    }
    errors: dict[str, Any] = {}
    try:
        instance.full_clean(
            exclude=set(trusted) | (same & relations),
            validate_constraints=False,
        )
    except ValidationError as error:
        errors = error.update_error_dict(errors)
    # As Django does, constraints skip fields that already failed.
    failed = {name for name in errors if name != NON_FIELD_ERRORS}
    for model, constraints in instance.get_constraints():
        for constraint in constraints:
            fields = _constraint_fields(constraint)
            if constraint.name in checked or (
                before is not None and fields is not None and fields <= same
            ):
                continue
            try:
                constraint.validate(model, instance, exclude=failed)
            except ValidationError as error:
                errors = error.update_error_dict(errors)
    if errors:
        raise ValidationError(errors)
