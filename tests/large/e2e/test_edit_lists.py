"""Editable lists: reorder by handle, remove with ×, add (design review)."""

from typing import Any

from playwright.sync_api import expect
import pytest

from cues.services import demo
from tests.large.e2e.conftest import Check
from tests.large.e2e.conftest import SignInAs

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def test_reorder_remove_and_add_statuses(
    page: Any,
    app: str,
    sign_in_as: SignInAs,
    axe: Check,
    csp_violations: list[str],
) -> None:
    del csp_violations
    project = demo.build_demo().project
    unused = project.statuses.create(
        label="Unused", kind="to_do", color="#123456", order=99
    )
    sign_in_as("Casey Composer")
    page.goto(f"{app}/projects/{project.pk}/settings/statuses/")
    table = page.locator("[data-inline-table]").first
    labels = table.locator("input[name$='-label']")
    first, second = labels.nth(0).input_value(), labels.nth(1).input_value()
    axe("statuses list", within="[data-inline-table]")
    # The Remove boxes give way to × buttons; the order to a handle.
    expect(table.locator("input[name$='-DELETE']").first).to_be_hidden()
    table.get_by_role("button", name="Move row 2", exact=True).press(
        "Alt+ArrowUp"
    )
    expect(labels.nth(0)).to_have_value(second)
    table.locator(
        "tr", has=page.locator(f"input[value='{unused.label}']")
    ).get_by_role("button", name="Remove row").click()
    page.get_by_role("button", name="Add Status").click()
    table.locator("input[name$='-label']").last.fill("Mixing")
    table.locator("select[name$='-kind']").last.select_option("done")
    page.get_by_role("button", name="Save").click()
    expect(page.get_by_text("Saved")).to_be_visible()
    statuses = list(
        project.statuses.order_by("order").values_list("label", flat=True)
    )
    assert statuses[:2] == [second, first]
    assert "Unused" not in statuses
    assert "Mixing" in statuses
