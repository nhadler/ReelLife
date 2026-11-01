"""The sign-in page and its error pages pass axe under the strict CSP."""

from typing import Any

from playwright.sync_api import expect
import pytest

from tests.large.e2e.conftest import Check

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def test_sign_in_and_a_failed_sign_in_read_well(
    page: Any, app: str, axe: Check, csp_violations: list[str]
) -> None:
    del csp_violations
    page.goto(f"{app}/auth/login/")
    expect(page.get_by_role("heading", level=1)).to_have_text("ReelLife")
    expect(
        page.get_by_role("link", name="Sign In with PocketID")
    ).to_be_visible()
    # The cue strip is decoration: out of the accessibility tree.
    expect(page.locator("svg.strip")).to_have_attribute("aria-hidden", "true")
    axe("sign in")
    page.goto(f"{app}/auth/oidc/callback/")
    expect(page.get_by_role("heading", level=1)).to_have_text(
        "Sign-In Didn't Complete"
    )
    axe("failed sign in")
