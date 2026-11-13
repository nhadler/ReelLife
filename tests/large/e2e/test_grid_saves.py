"""Grid saves under a slow or missing network.

Each cue's edits save one after another, so a second quick edit sends
the revision the first one got back instead of conflicting with it; a
save that can't reach the server says so instead of looking saved.
"""

import re
from typing import Any

from playwright.sync_api import expect
import pytest

from cues import models
from cues.services import demo
from tests.large.e2e.conftest import SignInAs

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _open(page: Any, app: str, sign_in_as: SignInAs) -> models.Cue:
    project = demo.build_demo().project
    sign_in_as("Casey Composer")
    page.goto(f"{app}/projects/{project.pk}/")
    expect(page.locator("#cue-grid[data-ready=true]")).to_be_attached()
    return project.cues.get(number="1m01")


def _type_title(page: Any, cue: models.Cue, text: str) -> None:
    cell = page.locator(f".ag-row[row-id='{cue.pk}'] [col-id=title]").first
    cell.dblclick()
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type(text)
    page.keyboard.press("Enter")


def test_quick_edits_to_one_cue_save_in_turn(
    page: Any, app: str, sign_in_as: SignInAs, csp_violations: list[str]
) -> None:
    del csp_violations
    cue = _open(page, app, sign_in_as)
    held: list[Any] = []

    def hold(route: Any) -> None:  # playwright wants a function here
        held.append(route)

    page.route(f"**/api/cues/{cue.pk}/", hold)
    _type_title(page, cue, "First Pass")
    _type_title(page, cue, "Second Pass")
    page.wait_for_timeout(300)
    assert len(held) == 1  # the second waits for the first's answer
    saved = f"/api/cues/{cue.pk}/"
    with page.expect_response(lambda reply: saved in reply.url):
        held.pop().continue_()
    for _ in range(50):
        if held:
            break
        page.wait_for_timeout(100)
    with page.expect_response(lambda reply: saved in reply.url) as second:
        held.pop().continue_()
    assert second.value.status == 200
    status = page.locator("#grid-status")
    expect(status).to_have_text("Saved 1m01.")
    expect(status).not_to_have_class(re.compile("grid-status--error"))
    cue.refresh_from_db()
    assert cue.title == "Second Pass"


def test_a_save_without_a_network_says_it_wasnt_saved(
    page: Any, app: str, sign_in_as: SignInAs, csp_violations: list[str]
) -> None:
    del csp_violations
    cue = _open(page, app, sign_in_as)
    page.context.set_offline(True)
    _type_title(page, cue, "Lost Signal")
    status = page.locator("#grid-status")
    expect(status).to_contain_text("Couldn't reach ReelTime")
    expect(status).to_contain_text("you typed “Lost Signal”")
    expect(status).to_have_class(re.compile("grid-status--error"))
    page.context.set_offline(False)
    cue.refresh_from_db()
    assert cue.title != "Lost Signal"
