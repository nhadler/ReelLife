"""The grid loads its cues over a poor connection or a lapsed session.

A try at the rows that fails to arrive or gets a server error is made
again, so the grid fills instead of staying empty until a reload. A page
left open past its session's end says it's signed out.
"""

import re
from typing import Any

from django.conf import settings
from playwright.sync_api import expect
import pytest

from accounts.sessions import SIGNED_OUT
from cues.services import demo
from tests.large.e2e.conftest import SignInAs

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _fail_first(page: Any, project_id: int, fail: str) -> list[str]:
    """Makes the first request for the rows fail; returns what happened."""
    seen: list[str] = []

    def handle(route: Any) -> None:  # playwright wants a function here
        if not seen:
            seen.append(fail)
            if fail == "network":
                route.abort("connectionreset")
            else:
                route.fulfill(status=502, body="Bad gateway")
            return
        seen.append("passed")
        route.continue_()

    page.route(f"**/api/projects/{project_id}/grid/", handle)
    return seen


@pytest.mark.parametrize("fail", ["network", "server"])
def test_the_grid_tries_again_when_its_rows_dont_arrive(
    page: Any,
    app: str,
    sign_in_as: SignInAs,
    csp_violations: list[str],
    fail: str,
) -> None:
    del csp_violations
    project = demo.build_demo().project
    sign_in_as("Casey Composer")
    seen = _fail_first(page, project.pk, fail)
    page.goto(f"{app}/projects/{project.pk}/")
    expect(page.locator("#cue-grid[data-ready=true]")).to_be_attached()
    first = project.cues.order_by("number").first()
    assert first is not None
    expect(page.locator(f".ag-row[row-id='{first.pk}']").first).to_be_visible()
    assert seen == [fail, "passed"]
    expect(page.locator("#grid-status")).not_to_contain_text("trying again")


def test_a_refused_load_isnt_tried_again(
    page: Any, app: str, sign_in_as: SignInAs, csp_violations: list[str]
) -> None:
    del csp_violations
    project = demo.build_demo().project
    sign_in_as("Casey Composer")
    tries: list[int] = []

    def refuse(route: Any) -> None:  # playwright wants a function here
        tries.append(1)
        route.fulfill(status=403, body="{}", content_type="application/json")

    page.route(f"**/api/projects/{project.pk}/grid/", refuse)
    page.goto(f"{app}/projects/{project.pk}/")
    expect(page.locator("#grid-status")).to_have_text(
        "The cue grid failed to load. Reload the page."
    )
    page.wait_for_timeout(1500)
    assert len(tries) == 1


def test_a_page_left_open_past_its_session_says_so(
    page: Any, app: str, sign_in_as: SignInAs, csp_violations: list[str]
) -> None:
    del csp_violations
    project = demo.build_demo().project
    sign_in_as("Casey Composer")
    page.goto(f"{app}/projects/{project.pk}/")
    expect(page.locator("#cue-grid[data-ready=true]")).to_be_attached()
    cue = project.cues.get(number="1m01")
    # The session ends; the CSRF cookie outlives it, as in real use.
    page.context.clear_cookies(name=settings.SESSION_COOKIE_NAME)
    cell = page.locator(f".ag-row[row-id='{cue.pk}'] [col-id=title]").first
    cell.dblclick()
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type("After Hours")
    page.keyboard.press("Enter")
    status = page.locator("#grid-status")
    expect(status).to_contain_text(SIGNED_OUT)
    expect(status).to_contain_text("not saved")
    # Reloading goes to sign in.
    page.reload()
    expect(page).to_have_url(re.compile(r"/auth/login/"))


def test_a_grid_that_loads_after_the_session_ended_says_so(
    page: Any, app: str, sign_in_as: SignInAs, csp_violations: list[str]
) -> None:
    del csp_violations
    project = demo.build_demo().project
    sign_in_as("Casey Composer")

    def signed_out(route: Any) -> None:  # playwright wants a function here
        # It ends while the page loads.
        page.context.clear_cookies(name=settings.SESSION_COOKIE_NAME)
        route.continue_()

    page.route(f"**/api/projects/{project.pk}/grid/", signed_out)
    page.goto(f"{app}/projects/{project.pk}/")
    expect(page.locator("#grid-status")).to_have_text(SIGNED_OUT)


def _drop_confirm_js(page: Any, times: int) -> list[str]:
    """Drops the first `times` requests for confirm.js (grid.js imports it)."""
    seen: list[str] = []

    def handle(route: Any) -> None:  # playwright wants a function here
        seen.append(route.request.url)
        if len(seen) <= times:
            route.abort("connectionreset")
        else:
            route.continue_()

    confirm = re.compile(r"/static/cues/confirm(\.[0-9a-f]{12})?\.js$")
    page.route(confirm, handle)
    return seen


def _page_loads(page: Any) -> list[str]:
    """Records each page the tab loads, reloads included."""
    loads: list[str] = []

    def record(request: Any) -> None:
        if request.resource_type == "document":
            loads.append(request.url)

    page.on("request", record)
    return loads


def test_a_dropped_script_reloads_the_page_once(
    page: Any, app: str, sign_in_as: SignInAs, csp_violations: list[str]
) -> None:
    del csp_violations
    project = demo.build_demo().project
    sign_in_as("Casey Composer")
    seen = _drop_confirm_js(page, times=1)
    loads = _page_loads(page)
    page.goto(f"{app}/projects/{project.pk}/")
    expect(page.locator("#cue-grid[data-ready=true]")).to_be_attached()
    assert len(seen) == 2 and len(loads) == 2  # dropped, then reloaded
    expect(page.locator("#load-guard")).to_have_count(0)


def test_a_script_that_keeps_failing_says_so(
    page: Any, app: str, sign_in_as: SignInAs, csp_violations: list[str]
) -> None:
    del csp_violations
    project = demo.build_demo().project
    sign_in_as("Casey Composer")
    _drop_confirm_js(page, times=99)
    loads = _page_loads(page)
    page.goto(f"{app}/projects/{project.pk}/")
    expect(page.locator("#load-guard")).to_have_text(
        "Part of this page didn't load. Reload the page."
    )
    page.wait_for_timeout(1000)
    assert len(loads) == 2  # one reload, then no more


def _hold_confirm_js(page: Any, times: int) -> list[Any]:
    """Never answers the first `times` requests for confirm.js."""
    held: list[Any] = []

    def handle(route: Any) -> None:  # playwright wants a function here
        if len(held) < times:
            held.append(route)  # no answer, ever: a stuck request
        else:
            route.continue_()

    confirm = re.compile(r"/static/cues/confirm(\.[0-9a-f]{12})?\.js$")
    page.route(confirm, handle)
    return held


def test_a_stuck_script_reloads_the_page_once(
    page: Any, app: str, sign_in_as: SignInAs, csp_violations: list[str]
) -> None:
    del csp_violations
    project = demo.build_demo().project
    sign_in_as("Casey Composer")
    held = _hold_confirm_js(page, times=1)
    loads = _page_loads(page)
    page.clock.install()
    page.goto(f"{app}/projects/{project.pk}/", wait_until="commit")
    page.wait_for_timeout(500)
    assert len(held) == 1 and len(loads) == 1
    expect(page.locator("#cue-grid[data-ready=true]")).to_have_count(0)
    page.clock.run_for(16000)  # past the 15 s the guard allows
    page.clock.resume()
    expect(page.locator("#cue-grid[data-ready=true]")).to_be_attached()
    assert len(loads) == 2
    expect(page.locator("#load-guard")).to_have_count(0)


def test_a_script_that_stays_stuck_says_so(
    page: Any, app: str, sign_in_as: SignInAs, csp_violations: list[str]
) -> None:
    del csp_violations
    project = demo.build_demo().project
    sign_in_as("Casey Composer")
    _hold_confirm_js(page, times=99)
    loads = _page_loads(page)
    page.clock.install()
    page.goto(f"{app}/projects/{project.pk}/", wait_until="commit")
    page.wait_for_timeout(500)
    page.clock.run_for(16000)
    page.wait_for_timeout(500)
    page.clock.run_for(16000)
    expect(page.locator("#load-guard")).to_have_text(
        "Part of this page didn't load. Reload the page."
    )
    assert len(loads) == 2  # one reload, then no more
