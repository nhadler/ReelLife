"""The Schedule page in a real browser, under axe and the CSP (P6)."""

import datetime
from typing import Any

from django.utils import timezone
from playwright.sync_api import expect
import pytest

from cues import models
from cues.services import demo
from tests.large.e2e.conftest import Check
from tests.large.e2e.conftest import SignInAs

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def test_the_month_and_week(
    page: Any,
    sign_in_as: SignInAs,
    axe: Check,
    csp_violations: list[str],
) -> None:
    del csp_violations
    project = demo.build_demo().project
    project.deadlines.all().delete()
    models.Deadline.objects.create(
        project=project,
        kind=models.Deadline.Kind.COMPOSITION,
        end_date=timezone.localdate() + datetime.timedelta(days=30),
    )
    sign_in_as("Casey Composer")
    # Home is the Overview: the next seven days, then the projects.
    expect(page.locator("li.cal-day")).to_have_count(7)
    expect(page.get_by_role("heading", name="Active Projects")).to_be_visible()
    axe("overview")
    page.get_by_role("link", name="Schedule", exact=True).click()
    expect(
        page.get_by_role("heading", name="Schedule", level=1)
    ).to_be_visible()
    expect(page.locator(".cal-day--today .cal-cue").first).to_be_visible()
    expect(page.locator(".day-cue").first).to_be_visible()
    axe("schedule month")
    # Anywhere in a day chooses it; its work is listed beside the calendar.
    later = page.locator("li.cal-day:not(.cal-day--past)").nth(3)
    name = later.locator(".visually-hidden").inner_text().split(",")[0]
    later.locator(".cal-day__music, .cal-day__date").first.click()
    expect(page.locator("#schedule-day-title")).to_contain_text(name)
    # Unticking a project hides its work at once.
    page.get_by_role("checkbox", name=project.title).uncheck()
    expect(page.locator(".cal-cue")).to_have_count(0)
    expect(page.get_by_role("checkbox", name=project.title)).not_to_be_checked()
    page.get_by_role("checkbox", name=project.title).check()
    expect(page.locator(".cal-cue").first).to_be_visible()
    page.get_by_role("link", name="Week", exact=True).click()
    expect(page.locator("li.cal-day")).to_have_count(7)
    axe("schedule week")
