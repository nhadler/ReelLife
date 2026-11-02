"""Deletion confirmation uses the app's dialog, not the browser's."""

from typing import Any

from playwright.sync_api import expect
import pytest

from cues.services import demo
from tests.large.e2e.conftest import SignInAs

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def test_deleting_a_cue_asks_first(
    page: Any, app: str, sign_in_as: SignInAs, csp_violations: list[str]
) -> None:
    del csp_violations
    project = demo.build_demo().project
    cue = project.cues.get(number="1m02")
    sign_in_as("Casey Composer")
    page.goto(f"{app}/cues/{cue.pk}/")
    page.locator("summary[aria-label='More for 1m02']").click()
    page.get_by_role("button", name="Delete Cue").click()
    confirm = page.locator(".confirm-dialog")
    expect(confirm).to_contain_text("Delete cue 1m02")
    confirm.get_by_role("button", name="Cancel").click()
    expect(confirm).to_be_hidden()
    assert project.cues.filter(pk=cue.pk).exists()
    page.locator("summary[aria-label='More for 1m02']").click()
    page.get_by_role("button", name="Delete Cue").click()
    confirm.get_by_role("button", name="Delete Cue").click()
    expect(page).to_have_url(f"{app}/projects/{project.pk}/")
    assert not project.cues.filter(pk=cue.pk).exists()
