"""Log Written from the grid's toolbar: on once one cue is ticked."""

from typing import Any

from playwright.sync_api import expect
import pytest

from cues.services import demo
from tests.large.e2e.conftest import Check
from tests.large.e2e.conftest import SignInAs

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _tick(page: Any, number: str) -> None:
    page.locator(".ag-row", has_text=number).first.locator(
        "[col-id=ag-Grid-SelectionColumn] input"
    ).check()


def test_log_written_waits_for_one_cue(
    page: Any,
    app: str,
    sign_in_as: SignInAs,
    axe: Check,
    csp_violations: list[str],
) -> None:
    del csp_violations
    project = demo.build_demo().project
    sign_in_as("Casey Composer")
    page.goto(f"{app}/projects/{project.pk}/")
    expect(page.locator("#cue-grid[data-ready=true]")).to_be_attached()
    button = page.get_by_role("button", name="Log Written")
    expect(button).to_be_disabled()
    _tick(page, "1m01")
    expect(button).to_be_enabled()
    _tick(page, "1m02")
    expect(button).to_be_disabled()  # one cue at a time
    page.locator("#grid-selection").get_by_role(
        "button", name="deselect all"
    ).click()
    _tick(page, "1m01")
    button.click()
    dialog = page.locator("#app-dialog")
    expect(
        dialog.get_by_role("heading", name="Log Written: 1m01")
    ).to_be_visible()
    axe("log written", within="#app-dialog")
    dialog.get_by_label("Written").fill("1:20")
    dialog.get_by_role("button", name="Log", exact=True).click()
    expect(dialog).to_be_hidden()
