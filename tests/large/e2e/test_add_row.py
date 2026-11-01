"""Add on editable lists: three writers in one save."""

from typing import Any

from playwright.sync_api import expect
import pytest

from cues import models
from cues.services import demo
from tests.large.e2e.conftest import Check
from tests.large.e2e.conftest import SignInAs

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def test_three_writers_in_one_save(
    page: Any,
    app: str,
    sign_in_as: SignInAs,
    axe: Check,
    csp_violations: list[str],
) -> None:
    del csp_violations
    cue = demo.build_demo().project.cues.get(number="1m01")
    cue.shares.all().delete()
    writers = [
        models.Party.objects.create(
            first_name=first, last_name="Writer", kind=models.Party.Kind.WRITER
        )
        for first in ("Ann", "Bea", "Cy")
    ]
    sign_in_as("Casey Composer")
    page.goto(f"{app}/cues/{cue.pk}/")
    section = page.locator("#shares-group")
    rows = section.locator("tbody tr")
    expect(rows).to_have_count(0)  # no blank spare rows
    expect(section.get_by_text("None yet.")).to_be_visible()
    for _ in range(3):
        section.get_by_role("button", name="Add Share").click()
    expect(rows).to_have_count(3)
    expect(section.locator("[name=shares-2-party]")).to_be_focused()
    axe("cue credits with an added row", within="#shares-group")
    for i, (writer, share) in enumerate(
        zip(writers, ("50", "25", "25"), strict=True)
    ):
        section.locator(f"[name=shares-{i}-party]").select_option(
            str(writer.pk)
        )
        section.locator(f"[name=shares-{i}-role]").select_option("C")
        section.locator(f"[name=shares-{i}-share]").fill(share)
    section.get_by_role("button", name="Save Shares").click()
    expect(page.locator("#shares-group [role=status]")).to_have_text("Saved.")
    assert sorted(str(s.share) for s in cue.shares.all()) == [
        "25.00",
        "25.00",
        "50.00",
    ]
