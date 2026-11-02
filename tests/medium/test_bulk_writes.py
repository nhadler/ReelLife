"""Imports and batch edits write many cues in few queries.

Each cue still gets its own history rows, so undo is unchanged; what's
gone is re-checking, for every cue, rows the write already holds.
"""

from collections.abc import Callable
import datetime
from typing import Any

from django.core.exceptions import ValidationError
from django.db.models import Q
import pytest

from cues import model_base
from cues import models
from cues.api import edits
from cues.importers import apply
from cues.importers import base
from cues.importers import merge
from cues.services import history
from tests import factories

pytestmark = pytest.mark.django_db
HOUR = 86_400  # frames at 24 fps
COUNT = 40


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    """A 24 fps film with reel 1 logged in a turnover."""
    project = factories.ProjectFactory.create(frame_rate="24")
    turnover = factories.TurnoverFactory.create(
        project=project, received_date=datetime.date(2026, 9, 17)
    )
    picture = factories.PictureFactory.create(project=project, name="1")
    factories.PictureVersionFactory.create(
        picture=picture, turnover=turnover, tc_in=HOUR, tc_out=2 * HOUR
    )
    return project


def _plan(project: models.Project, *numbers: str) -> merge.Preview:
    rows = [
        base.ImportRow(
            label=number,
            number=number,
            title=f"Cue {i}",
            tc_in=HOUR + i * 100,
            tc_out=HOUR + i * 100 + 50,
            fields={"spotting_notes": "door closes"},
        )
        for i, number in enumerate(numbers)
    ]
    return merge.plan(
        project, base.ParsedFile(source="csv", rows=rows), merge.Options()
    )


def _numbers(count: int) -> list[str]:
    return [f"1m{i:02d}" for i in range(1, count + 1)]


def test_an_import_costs_a_few_queries_a_cue(
    project: models.Project, django_assert_max_num_queries: Callable[..., Any]
) -> None:
    preview = _plan(project, *_numbers(COUNT))
    # Two inserts and their history rows per cue and version, plus the
    # current version link, the shares check and a little setup.
    with django_assert_max_num_queries(8 * COUNT + 20):
        result = apply.apply(project, preview, filename="cues.csv")
    assert result.created == COUNT
    cue = project.cues.get(number="1m01")
    assert cue.current_version is not None
    assert cue.current_version.written_to is not None
    assert cue.current_version.spotting_notes == "door closes"
    # Still one change set, undone in one step.
    history.undo(result.change_set)
    assert not project.cues.exists()


def test_reimporting_updates_in_a_few_queries_a_cue(
    project: models.Project, django_assert_max_num_queries: Callable[..., Any]
) -> None:
    apply.apply(project, _plan(project, *_numbers(COUNT)))
    rows = [
        base.ImportRow(
            label=n,
            number=n,
            title="",
            tc_in=HOUR + i * 200,
            tc_out=HOUR + i * 200 + 50,
        )
        for i, n in enumerate(_numbers(COUNT))
    ]
    preview = merge.plan(
        project, base.ParsedFile(source="csv", rows=rows), merge.Options()
    )
    with django_assert_max_num_queries(4 * COUNT + 20):
        result = apply.apply(project, preview)
    assert result.updated == COUNT - 1  # 1m01 stays put
    assert project.cues.get(number="1m02").tc_in == HOUR + 200


def test_a_number_taken_since_the_preview_is_refused(
    project: models.Project,
) -> None:
    preview = _plan(project, "1m01", "1M02")
    picture = project.pictures.get(name="1")
    factories.CueFactory.create(project=project, picture=picture, number="1m02")
    with pytest.raises(ValidationError) as caught:
        apply.apply(project, preview)
    assert caught.value.messages == [
        "1M02: Another active cue already has this number."
    ]
    assert list(project.cues.values_list("number", flat=True)) == ["1m02"]


def test_a_cue_deleted_since_the_preview_is_refused(
    project: models.Project,
) -> None:
    apply.apply(project, _plan(project, "1m01"))
    preview = _plan(project, "1m00", "1m01")  # 1m01 moves: an update
    assert preview.changes[1].action == merge.Action.UPDATE
    project.cues.get(number="1m01").delete()
    with pytest.raises(ValidationError, match="Preview the file again"):
        apply.apply(project, preview)


def test_a_picture_deleted_since_the_preview_is_refused(
    project: models.Project,
) -> None:
    extra = factories.PictureFactory.create(project=project, name="9")
    rows = [
        base.ImportRow(label="9m01", number="9m01", title="", tc_in=1, tc_out=2)
    ]
    preview = merge.plan(
        project,
        base.ParsedFile(source="csv", rows=rows),
        merge.Options(picture_id=extra.pk),
    )
    extra.delete()
    with pytest.raises(ValidationError, match="Preview the file again"):
        apply.apply(project, preview)


@pytest.fixture(name="cues")
def fixture_cues(project: models.Project) -> list[models.Cue]:
    apply.apply(project, _plan(project, *_numbers(COUNT)))
    return list(
        project.cues.select_related("project", "current_version", "picture")
    )


@pytest.mark.parametrize(
    ("field", "per_cue"), [("status", 5), ("notes", 5), ("priority", 4)]
)
def test_a_batch_edit_costs_a_few_queries_a_cue(
    cues: list[models.Cue],
    field: str,
    per_cue: int,
    django_assert_max_num_queries: Callable[..., Any],
) -> None:
    project = cues[0].project
    value: Any = {
        "status": project.statuses.get(kind=models.StatusKind.DONE).pk,
        "notes": "louder",
        "priority": "High",
    }[field]
    with django_assert_max_num_queries(per_cue * COUNT + 15):
        edits.apply_many(cues, field, value)
    change_set = models.ChangeSet.objects.latest("pk")
    history.undo(change_set)


def test_batch_reactivating_a_duplicate_number_is_refused(
    cues: list[models.Cue],
) -> None:
    # The unique number only binds active cues, so turning one back on
    # must still be checked even though the number didn't change.
    old = cues[0]
    edits.apply(old, "active", False)
    copy = factories.CueFactory.create(
        project=old.project, picture=old.picture, number=old.number
    )
    del copy
    with pytest.raises(edits.EditError, match="already has this number"):
        edits.apply_many([old, cues[1]], "active", True)


def test_full_clean_skips_only_what_it_may(cues: list[models.Cue]) -> None:
    cue, other = cues[0], cues[1]
    before = model_base.snapshot(cue)
    cue.number = other.number  # changed: its unique check still runs
    with pytest.raises(ValidationError, match="already has this number"):
        model_base.full_clean(cue, before)
    model_base.full_clean(  # unless the caller says it checked
        cue, before, checked=("cue_number_unique_among_active",)
    )
    cue.number, cue.cue_type = before["number"], "nonsense"
    with pytest.raises(ValidationError) as caught:
        model_base.full_clean(cue, before)
    assert list(caught.value.message_dict) == ["cue_type"]


def test_a_frame_check_with_another_lookup_asks_the_database(
    cues: list[models.Cue],
) -> None:
    check = model_base.FrameCheck(
        condition=Q(tc_in__lte=5), name="t", violation_error_message="Early."
    )
    with pytest.raises(ValidationError, match="Early."):
        check.validate(models.Cue, cues[0])
    late = model_base.FrameCheck(condition=Q(tc_in__lte=99 * HOUR), name="l")
    late.validate(models.Cue, cues[0])


def test_undo_costs_a_few_queries_a_cue(
    cues: list[models.Cue], django_assert_max_num_queries: Callable[..., Any]
) -> None:
    project = cues[0].project
    imported = models.ChangeSet.objects.get(project=project)
    edits.apply_many(cues, "notes", "louder")
    batch = models.ChangeSet.objects.latest("pk")
    # Each cue: the writes, their history rows and a savepoint each.
    with django_assert_max_num_queries(9 * COUNT + 30):
        history.undo(batch)
    with django_assert_max_num_queries(7 * COUNT + 40):
        history.undo(imported)
    assert not project.cues.exists()
    redo = models.ChangeSet.objects.latest("pk")
    history.undo(redo)  # the undo undoes too
    assert project.cues.count() == COUNT
