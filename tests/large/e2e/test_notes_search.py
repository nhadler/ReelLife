"""Finding cues by their notes: the grid's find field and Ctrl+K."""

import re
from typing import Any

from playwright.sync_api import expect
import pytest

from cues import models
from cues.services import demo
from tests.large.e2e.conftest import Check
from tests.large.e2e.conftest import SignInAs

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _rows(page: Any) -> Any:
    """The cue rows (reel group rows are rows too)."""
    return page.locator(".ag-row:not(.group-row-node)")


def test_notes_find_cues_in_the_grid_and_ctrl_k(
    page: Any,
    app: str,
    sign_in_as: SignInAs,
    axe: Check,
    csp_violations: list[str],
) -> None:
    del csp_violations
    project = demo.build_demo().project
    cues = list(project.cues.filter(active=True).order_by("number"))
    spotted, revised = cues[0], cues[1]
    # The first cue's current version mentions the foghorn ...
    current = spotted.current_version
    assert current is not None
    current.spotting_notes = "Foghorn as the door closes."
    current.save()
    # ... and the second's older version did, before a new one.
    older = revised.current_version
    assert older is not None
    older.notes = "Foghorn over the storm, then strings."
    older.save()
    revised.current_version = models.CueVersion.objects.create(
        cue=revised, label="v9", notes="Strings only."
    )
    revised.save()

    sign_in_as("Casey Composer")
    page.goto(f"{app}/projects/{project.pk}/")
    expect(_rows(page).first).to_be_visible()

    # The grid matches the current version's notes, every word.
    page.get_by_role("button", name="Find a cue").click()
    search = page.get_by_role("searchbox", name=re.compile("^Find a cue"))
    search.fill("foghorn door")
    expect(_rows(page)).to_have_count(1)
    expect(_rows(page).first).to_contain_text(spotted.number)
    search.fill("foghorn storm")
    expect(_rows(page)).to_have_count(0)
    search.fill("")

    # Ctrl+K looks in every version and says where it matched.
    page.keyboard.press("Control+k")
    palette = page.locator("#palette")
    expect(palette).to_be_visible()
    palette.get_by_role("combobox").fill("foghorn")
    options = palette.get_by_role("option")
    expect(options).to_have_count(2)
    expect(options.nth(0)).to_contain_text(
        f"Spotting Notes ({current.label}): Foghorn as the door closes."
    )
    expect(options.nth(1)).to_contain_text(
        f"Notes ({older.label}): Foghorn over the storm, then strings."
    )
    axe("search palette with notes matches")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(rf"\?version={older.pk}$"))
    expect(
        page.get_by_role("heading", name=f"Version {older.label}", exact=True)
    ).to_be_visible()
