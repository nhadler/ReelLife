"""Tall grid rows: every cell starts on the same line.

A cue's notes can wrap a row to several lines. Text, checkboxes and the
active eye all sit at the top of the row then, not some at the top and
some in the middle.
"""

from typing import Any

from playwright.sync_api import expect
import pytest

from cues import models
from cues.services import demo
from tests.large.e2e.conftest import SignInAs

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

# How far apart (px) the middles of two cells' first lines may be.
SLACK = 4

# The middle of each cell's first line of text, or of its control,
# relative to the row (drawn in parts: pinned columns and the rest).
_MIDDLES = """(rows) => {
  const top = rows[0].getBoundingClientRect().top;
  const first = (cell) => {
    const control = cell.querySelector('input, button');
    if (control) return control.getBoundingClientRect();
    const walk = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
    for (let node = walk.nextNode(); node; node = walk.nextNode()) {
      if (node.textContent.trim()) {
        const range = document.createRange();
        range.selectNodeContents(node);
        return range.getClientRects()[0];
      }
    }
    return null;
  };
  const middles = {};
  const cells = rows.flatMap((row) => [...row.querySelectorAll('.ag-cell')]);
  for (const cell of cells) {
    const box = first(cell);
    if (box) {
      middles[cell.getAttribute('col-id')] = box.top + box.height / 2 - top;
    }
  }
  return middles;
}"""


def test_a_tall_rows_cells_all_start_on_its_first_line(
    page: Any, app: str, sign_in_as: SignInAs, csp_violations: list[str]
) -> None:
    del csp_violations
    project = demo.build_demo().project
    cue = project.cues.get(number="1m01")
    cue.versions.update(
        notes=" ".join(["A long note that wraps the row."] * 12)
    )
    cue.plays_as_source = True
    cue.save()
    models.View.objects.create(
        project=project,
        name="Tall",
        columns=[
            {"id": "number"},
            {"id": "notes", "width": 200},
            {"id": "plays_as_source"},
            {"id": "interrupted_use"},
            {"id": "_active"},
        ],
    )
    sign_in_as("Casey Composer")
    page.goto(f"{app}/projects/{project.pk}/")
    expect(page.locator("#cue-grid[data-ready=true]")).to_be_attached()
    page.get_by_role("button", name="Tall", exact=True).click()
    row = page.locator(f".ag-row[row-id='{cue.pk}']")
    expect(row.locator("[col-id=plays_as_source] input")).to_be_attached()
    assert row.first.bounding_box()["height"] > 80
    middles = row.evaluate_all(_MIDDLES)
    shown = {"number", "notes", "plays_as_source", "interrupted_use"}
    assert shown | {"_active"} <= set(middles)
    assert max(middles.values()) - min(middles.values()) <= SLACK, sorted(
        middles.items(), key=lambda m: m[1]
    )


def test_no_pin_sticks_for_a_column_pinned_by_default(
    page: Any, app: str, sign_in_as: SignInAs, csp_violations: list[str]
) -> None:
    del csp_violations
    project = demo.build_demo().project
    sign_in_as("Casey Composer")
    page.goto(f"{app}/projects/{project.pk}/")
    expect(page.locator("#cue-grid[data-ready=true]")).to_be_attached()
    header = page.locator(".ag-header-cell[col-id=status]")
    no_pin = page.get_by_role("menuitem", name="No Pin")
    # Status is pinned by default, so No Pin is on offer.
    header.click(button="right")
    expect(no_pin).to_be_enabled()
    with page.expect_response(lambda r: "/views/" in r.url and r.ok):
        no_pin.click()
    standard = models.View.objects.get(project=project, name="Standard")
    status = next(c for c in standard.columns if c["id"] == "status")
    assert status["pinned"] == "none"
    # After a reload it's still unpinned.
    page.reload()
    expect(page.locator("#cue-grid[data-ready=true]")).to_be_attached()
    header.click(button="right")
    expect(no_pin).to_be_disabled()
    expect(page.get_by_role("menuitem", name="Pin Left")).to_be_enabled()
