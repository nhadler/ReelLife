"""Activity's Undo and Redo, and Trash's Restore, in a real browser."""

from typing import Any

from playwright.sync_api import expect
import pytest

from cues import models
from cues.services import authoring
from cues.services import demo
from cues.services import history
from tests.large.e2e.conftest import Check
from tests.large.e2e.conftest import SignInAs

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _retitle(cue: models.Cue, title: str) -> None:
    with history.recording(cue.project, f"Edit {cue.number} Title"):
        cue.title = title
        cue.save()


def test_undo_from_activity_and_restore_from_trash(
    page: Any,
    app: str,
    sign_in_as: SignInAs,
    axe: Check,
    csp_violations: list[str],
) -> None:
    del csp_violations
    project = demo.build_demo().project
    first = project.cues.get(number="1m01")
    second = project.cues.get(number="1m02")
    _retitle(first, "Morning")
    _retitle(second, "Evening")  # newer, on another cue
    doomed = project.cues.get(number="1m03")
    doomed_pk = doomed.pk
    authoring.delete_cue(doomed)

    sign_in_as("Casey Composer")
    page.goto(f"{app}/projects/{project.pk}/")
    page.locator(".project-menu__button").click()
    page.locator(".project-menu__panel").get_by_role(
        "link", name="Activity"
    ).click()
    expect(page.get_by_role("heading", name="Activity")).to_be_visible()
    axe("activity")

    # The older edit, not the latest, can be undone and redone.
    row = page.locator("tr", has_text="Edit 1m01 Title")
    row.get_by_role("button", name="Undo").click()
    expect(page.get_by_text("Undid Edit 1m01 Title.")).to_be_visible()
    first.refresh_from_db()
    assert first.title != "Morning"
    undo_row = page.locator("tr", has_text="Undo: Edit 1m01 Title")
    undo_row.get_by_role("button", name="Redo").click()
    expect(page.get_by_text("Redid Edit 1m01 Title.")).to_be_visible()
    first.refresh_from_db()
    assert first.title == "Morning"
    axe("activity after redo")

    page.get_by_role("link", name="Trash").click()
    expect(page.get_by_role("heading", name="Trash")).to_be_visible()
    axe("trash")
    page.locator("tr", has_text="Delete cue 1m03").get_by_role(
        "button", name="Restore"
    ).click()
    expect(page.get_by_text("Restored 1m03.")).to_be_visible()
    expect(page.get_by_text("The Trash is empty.")).to_be_visible()
    restored = models.Cue.objects.get(pk=doomed_pk)
    assert restored.current_version is not None
    axe("empty trash")
