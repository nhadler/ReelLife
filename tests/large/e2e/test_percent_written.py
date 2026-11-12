"""% Written in the grid: a bar per cue that follows Log Written."""

import math
from typing import Any

from playwright.sync_api import expect
import pytest

from cues import models
from cues import timecode
from cues.services import demo
from tests.large.e2e.conftest import Check
from tests.large.e2e.conftest import SignInAs

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def test_percent_written_follows_log_written(
    page: Any,
    app: str,
    sign_in_as: SignInAs,
    axe: Check,
    csp_violations: list[str],
) -> None:
    project = demo.build_demo().project
    cue = project.cues.get(number="1m01")
    cue.status = project.statuses.get(kind=models.StatusKind.IN_PROGRESS)
    cue.save(update_fields=["status"])
    written = project.cues.get(number="1m02")
    written.status = project.statuses.get(kind=models.StatusKind.DONE)
    written.save(update_fields=["status"])
    sign_in_as("Casey Composer")
    page.goto(f"{app}/projects/{project.pk}/")
    expect(page.locator("#cue-grid[data-ready=true]")).to_be_attached()

    # Right after Status in the Standard view.
    headers = page.locator(".ag-header-cell[col-id]").evaluate_all(
        "cells => cells"
        "  .sort((a, b) => a.getAttribute('aria-colindex')"
        "    - b.getAttribute('aria-colindex'))"
        "  .map(c => c.getAttribute('col-id'))"
    )
    assert headers[headers.index("status") + 1] == "percent_written"

    def cell(number: str) -> Any:
        return page.locator(
            f".ag-row[row-id='{project.cues.get(number=number).pk}'] "
            "[col-id=percent_written]"
        ).first

    expect(cell("1m01")).to_have_text("0%")
    expect(cell("1m02")).to_have_text("100%")
    expect(cell("1m02").locator(".written")).to_have_class(
        "written written--done"
    )
    axe("% Written cells", within="#cue-grid")

    # A click on a cue being written opens Log Written; logging half of
    # it moves the bar without a reload.
    cell("1m01").click()
    dialog = page.locator("#app-dialog")
    expect(dialog.get_by_role("heading")).to_contain_text("Log Written: 1m01")
    assert cue.tc_in is not None and cue.tc_out is not None
    length = timecode.seconds(cue.tc_out - cue.tc_in, project.rate)
    half = int(length) // 2
    dialog.get_by_label("Written").fill(f"{half // 60}:{half % 60:02d}")
    dialog.get_by_role("button", name="Log").click()
    expected = math.floor(half * 100 / float(length))
    expect(cell("1m01")).to_have_text(f"{expected}%")
    meter = cell("1m01").locator(".written")
    assert meter.evaluate("e => e.style.getPropertyValue('--written')") == str(
        expected
    )
    assert not csp_violations
