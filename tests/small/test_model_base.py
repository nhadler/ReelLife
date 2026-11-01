"""Frame checks evaluated in memory, with SQL's three-valued logic."""

# These test model_base's private helpers directly.
# pylint: disable=protected-access

from django.core.exceptions import ValidationError
from django.db import models as db_models
from django.db.models import F
from django.db.models import Q
from django.db.models.lookups import Exact
import pytest

from cues import model_base
from cues import models

OUT_NOT_BEFORE_IN = (
    Q(tc_in__isnull=True) | Q(tc_out__isnull=True) | Q(tc_out__gte=F("tc_in"))
)


@pytest.mark.parametrize(
    ("condition", "values", "truth"),
    [
        (Q(tc_in__gte=0), {"tc_in": 5}, True),
        (Q(tc_in__gte=0), {"tc_in": -1}, False),
        (Q(tc_in__gte=0), {"tc_in": None}, None),  # unknown passes in SQL
        (OUT_NOT_BEFORE_IN, {"tc_in": 10, "tc_out": 9}, False),
        (OUT_NOT_BEFORE_IN, {"tc_in": 10, "tc_out": None}, True),
        (Q(tc_in__gte=0) & Q(tc_out__gte=0), {"tc_in": 1, "tc_out": -1}, False),
        (
            Q(tc_in__gte=0) & Q(tc_out__gte=0),
            {"tc_in": 1, "tc_out": None},
            None,
        ),
        (Q(tc_in__gte=0) & Q(tc_out__gte=0), {"tc_in": 1, "tc_out": 2}, True),
        (
            Q(tc_in__gte=0) | Q(tc_out__gte=0),
            {"tc_in": -1, "tc_out": None},
            None,
        ),
        (~Q(tc_in__gte=0), {"tc_in": 5}, False),
        (~Q(tc_in__gte=0), {"tc_in": None}, None),
        (  # mixed connectors nest
            (Q(tc_in__isnull=True) | Q(tc_in__gte=0)) & Q(tc_out__gte=0),
            {"tc_in": None, "tc_out": 3},
            True,
        ),
    ],
)
def test_truth_follows_sql(
    condition: Q, values: dict[str, int | None], truth: bool | None
) -> None:
    assert model_base._truth(condition, values) is truth  # noqa: SLF001


def test_other_lookups_are_left_to_the_database() -> None:
    with pytest.raises(NotImplementedError):
        model_base._truth(Q(tc_in__lte=5), {"tc_in": 1})  # noqa: SLF001


def test_a_frame_check_refuses_out_before_in_without_a_query() -> None:
    check = model_base.FrameCheck(
        condition=OUT_NOT_BEFORE_IN,
        name="t",
        violation_error_message="OUT can't be before IN.",
    )
    cue = models.Cue(tc_in=100, tc_out=50)
    with pytest.raises(ValidationError, match="OUT can't be before IN."):
        check.validate(models.Cue, cue)
    # A field that already failed isn't judged again.
    check.validate(models.Cue, cue, exclude={"tc_out"})
    check.validate(models.Cue, models.Cue(tc_in=100, tc_out=None))


def test_a_frame_check_migrates_as_a_check_constraint() -> None:
    check = model_base.FrameCheck(condition=Q(tc_in__gte=0), name="t")
    path, _, kwargs = check.deconstruct()
    assert path == "django.db.models.CheckConstraint"
    assert kwargs == {"condition": Q(tc_in__gte=0), "name": "t"}


def test_constraint_fields() -> None:
    by_name = {c.name: c for c in models.Cue._meta.constraints}
    fields = model_base._constraint_fields  # noqa: SLF001
    assert fields(by_name["cue_number_unique_among_active"]) == {
        "project",
        "number",
        "active",
    }
    assert fields(by_name["cue_tc_out_not_before_in"]) == {"tc_in", "tc_out"}
    nested = (Q(tc_in__isnull=True) | Q(tc_in__gte=0)) & Q(tc_out__gte=0)
    assert model_base._fields(nested) == {"tc_in", "tc_out"}  # noqa: SLF001
    plain = db_models.UniqueConstraint(fields=["cue", "label"], name="u")
    assert fields(plain) == {"cue", "label"}

    class Other(db_models.BaseConstraint):  # pylint: disable=abstract-method
        pass

    assert fields(Other(name="other")) is None
    lookup = db_models.CheckConstraint(condition=Exact(F("tc_in"), 1), name="e")
    assert fields(lookup) is None
