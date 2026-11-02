"""The project's Settings menu: grid actions from any page."""

from typing import Any

from playwright.sync_api import expect
import pytest

from cues.services import demo
from tests.large.e2e.conftest import Check
from tests.large.e2e.conftest import SignInAs

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _open_menu(page: Any) -> Any:
    page.locator(".project-menu__button").click()
    return page.locator(".project-menu__panel")


def test_the_menu_reaches_the_grid_from_any_page(
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
    menu = _open_menu(page)
    axe("settings menu", within=".project-menu")
    menu.get_by_role("button", name="Export Cues").click()
    dialog = page.locator("#app-dialog")
    expect(dialog.get_by_role("heading", name="Export Cues")).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog.get_by_role("heading", name="Export Cues")).to_be_hidden()
    # The toolbar's Export opens the same dialog.
    page.locator("#grid-export-open").click()
    expect(dialog.get_by_role("heading", name="Export Cues")).to_be_visible()
    page.keyboard.press("Escape")
    # From the Picture page, Stats opens the grid with the panel up.
    page.goto(f"{app}/projects/{project.pk}/picture/")
    _open_menu(page).get_by_role("link", name="Stats").click()
    expect(page.locator("#stats-toggle")).to_have_attribute(
        "aria-expanded", "true"
    )
    assert "do=" not in page.url
