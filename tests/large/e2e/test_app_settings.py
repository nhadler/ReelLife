"""App Settings as one sheet that fits a laptop screen (Nick, Nov 1)."""

from typing import Any

from playwright.sync_api import expect
import pytest

from tests.large.e2e.conftest import Check
from tests.large.e2e.conftest import SignInAs

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def test_the_settings_fit_without_scrolling(
    page: Any,
    app: str,
    sign_in_as: SignInAs,
    axe: Check,
    csp_violations: list[str],
) -> None:
    del csp_violations
    page.set_viewport_size({"width": 1280, "height": 800})
    sign_in_as("Nick Admin")
    page.goto(f"{app}/settings/")
    expect(page.get_by_role("group", name="Schedule")).to_be_visible()
    expect(page.get_by_label("Projects at Once")).to_have_value("parallel")
    height = page.evaluate("document.documentElement.scrollHeight")
    assert height <= page.viewport_size["height"]
    save = page.get_by_role("button", name="Save").bounding_box()
    assert save["y"] + save["height"] <= page.viewport_size["height"]
    axe("app settings")
    # On a phone it scrolls, but never sideways.
    page.set_viewport_size({"width": 390, "height": 844})
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= 390
