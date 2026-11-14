"""Log Written from the grid's toolbar: on once one cue is ticked."""

from typing import Any

from playwright.sync_api import expect
import pytest

from cues import models
from cues import timecode
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
        "button", name="Deselect All"
    ).click()
    _tick(page, "1m01")
    button.click()
    dialog = page.locator("#app-dialog")
    expect(
        dialog.get_by_role("heading", name="Log Written: 1m01")
    ).to_be_visible()
    axe("log written", within="#app-dialog")
    # From the cue's IN to its OUT; log the first 20 seconds.
    cue = project.cues.get(number="1m01")
    assert cue.tc_in is not None and cue.tc_out is not None
    rate = project.rate

    def tc(frames: int) -> str:
        return timecode.format_timecode(frames, rate)

    stop = tc(cue.tc_in + 20 * rate.nominal)
    expect(dialog.get_by_label("From")).to_have_value(tc(cue.tc_in))
    expect(dialog.get_by_label("To")).to_have_value(tc(cue.tc_out))
    dialog.get_by_label("To").fill(stop)
    dialog.get_by_role("button", name="Log", exact=True).click()
    expect(dialog).to_be_hidden()
    log = models.WritingLog.objects.get(cue=cue)
    assert (log.seconds, log.start) == (20, 0)
    # Next time it carries on from there, and lists the log.
    button.click()
    expect(dialog.get_by_label("From")).to_have_value(stop)
    expect(dialog.locator(".log-written__list")).to_contain_text(
        f"{tc(cue.tc_in)} to {stop}"
    )
    axe("log written with a log", within="#app-dialog")
    # The list sits above the buttons, clear of them.
    listed = dialog.locator(".log-written__list").bounding_box()
    footer = dialog.locator(".form-actions").bounding_box()
    assert listed is not None and footer is not None
    assert listed["y"] + listed["height"] <= footer["y"]
    # Remove keeps the dialog open; Enter in a field still logs.
    dialog.get_by_role("button", name="Remove").click()
    expect(dialog.locator(".log-written__list")).to_have_count(0)
    assert not models.WritingLog.objects.filter(cue=cue).exists()
    # Focus lands on Day once htmx has wired up the new form.
    expect(dialog.get_by_label("Day")).to_be_focused()
    dialog.get_by_label("To").press("Enter")
    expect(dialog).to_be_hidden()
    assert models.WritingLog.objects.filter(cue=cue).count() == 1
