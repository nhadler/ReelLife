"""The grid's right-click menu on a cue."""

from typing import Any

from playwright.sync_api import expect
import pytest

from cues.services import demo
from tests.large.e2e.conftest import SignInAs

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def test_details_opens_the_cues_page(
    page: Any, app: str, sign_in_as: SignInAs, csp_violations: list[str]
) -> None:
    del csp_violations
    project = demo.build_demo().project
    cue = project.cues.get(number="1m02")
    sign_in_as("Casey Composer")
    page.goto(f"{app}/projects/{project.pk}/")
    expect(page.locator("#cue-grid[data-ready=true]")).to_be_attached()
    page.locator(".ag-row", has_text="1m02").first.locator(
        "[col-id=title]"
    ).click(button="right")
    menu = page.get_by_role("menu")
    # First in the menu, as the thing a cue is most often opened for.
    expect(menu.get_by_role("menuitem").first).to_contain_text("Details")
    menu.get_by_role("menuitem", name="Details").click()
    expect(page).to_have_url(f"{app}/cues/{cue.pk}/")
