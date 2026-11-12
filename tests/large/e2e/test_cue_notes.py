"""Cue notes in the panel beside the grid: formatted editing, check lists."""

import sys
from typing import Any

from playwright.sync_api import expect
import pytest

from cues import models
from cues.services import demo
from tests.large.e2e.conftest import Check
from tests.large.e2e.conftest import SignInAs

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

FIRST = (
    "## Spotting\nSee [the cut](https://example.com/cut).\n\n"
    "- [ ] Lower strings\n- [x] Kalimba out"
)
# Markup and a script link must show as text, never run.
HOSTILE = "<img src=x onerror=alert(1)> [click](javascript:alert(1)) **bold**"
# Check boxes pasted from elsewhere, and a list indented under a line.
PASTED = (
    "CHUNK 1\n☐ Make it sadder\n☑ Stay in that space\n\n"
    "NOTES\n    - Messy vibes"
)


def _notes(cue: models.Cue) -> str:
    cue.refresh_from_db()
    assert cue.current_version is not None
    return cue.current_version.revision_notes


def _set_up(page: Any, app: str, sign_in_as: SignInAs, who: str) -> list[Any]:
    project = demo.build_demo().project
    cues = list(project.cues.order_by("number")[:3])
    for cue, text in zip(cues, (FIRST, HOSTILE, PASTED), strict=True):
        assert cue.current_version is not None
        cue.current_version.revision_notes = text
        cue.current_version.save(update_fields=["revision_notes"])
    # The project's only view; viewers get the one named Viewer.
    models.View.objects.create(
        project=project,
        name="Viewer",
        columns=[
            {"id": "number"},
            {"id": "title"},
            {"id": "revision_notes", "width": 260},
        ],
    )
    sign_in_as(who)
    page.goto(f"{app}/projects/{project.pk}/")
    expect(page.locator("#cue-grid[data-ready=true]")).to_be_attached()
    return cues


def _cell(page: Any, cue: models.Cue) -> Any:
    return page.locator(
        f".ag-row[row-id='{cue.pk}'] [col-id=revision_notes]"
    ).first


def _saved(page: Any, cue: models.Cue, check: Any) -> None:
    """Waits until the cue's saved notes pass `check`."""
    for _ in range(50):
        if check(_notes(cue)):
            break
        page.wait_for_timeout(100)
    assert check(_notes(cue)), _notes(cue)
    expect(page.locator(".notes-panel__status")).to_have_text("Saved")


def _to_end(page: Any) -> None:
    """Puts the cursor at the end of the notes (keys differ on a Mac)."""
    page.keyboard.press(
        "Meta+ArrowDown" if sys.platform == "darwin" else "Control+End"
    )


def _to_line_end(page: Any) -> None:
    """Puts the cursor at the end of its line."""
    page.keyboard.press(
        "Meta+ArrowRight" if sys.platform == "darwin" else "End"
    )


def _select_back(page: Any, text: str) -> None:
    """Selects the text just typed (Shift+Home differs between systems)."""
    for _ in text:
        page.keyboard.press("Shift+ArrowLeft")


def test_writing_notes_as_formatted_text(
    page: Any,
    app: str,
    sign_in_as: SignInAs,
    axe: Check,
    csp_violations: list[str],
) -> None:
    first, _, _ = _set_up(page, app, sign_in_as, "Casey Composer")
    cell = _cell(page, first)
    # The cell shows the note formatted, led by its check-list progress.
    expect(cell.locator(".md-count")).to_have_text("1/2")
    expect(cell.locator(".md-heading")).to_have_text("Spotting")
    # Hovering shows the whole note in a card.
    cell.hover()
    expect(page.locator(".notes-card")).to_contain_text("Kalimba out")
    cell.click()
    panel = page.locator("#side-panel")
    expect(panel.locator(".side-panel__title")).to_contain_text(first.number)
    doc = panel.locator(".notes-panel__doc")
    # One view: formatted and editable, with no Write/Preview switch.
    expect(doc).to_be_focused()
    expect(doc.locator("h2")).to_have_text("Spotting")
    expect(panel.get_by_role("link", name="the cut")).to_have_attribute(
        "href", "https://example.com/cut"
    )
    expect(panel.get_by_role("button", name="Preview")).to_have_count(0)
    expect(panel.locator(".notes-panel__count")).to_have_text("1 of 2 done")
    axe("cue notes panel", within="#side-panel")
    # Opening a note doesn't rewrite it.
    page.wait_for_timeout(1200)
    assert _notes(first) == FIRST

    # Enter in a check list starts the next item; typing saves.
    doc.locator("p", has_text="Kalimba out").click()
    _to_line_end(page)
    page.keyboard.press("Enter")
    page.keyboard.type("Brass swell & horns")
    _saved(
        page,
        first,
        lambda t: t.endswith("- [x] Kalimba out\n- [ ] Brass swell & horns"),
    )
    expect(panel.locator(".notes-panel__count")).to_have_text("1 of 3 done")

    # The toolbar formats the selection.
    _select_back(page, "Brass swell & horns")
    panel.get_by_role("button", name="Bold").click()
    _saved(page, first, lambda t: t.endswith("- [ ] **Brass swell & horns**"))
    expect(doc.locator("strong")).to_have_text(["Brass swell & horns"])

    # Ticking a box saves it, and the cell's count follows.
    panel.get_by_role("checkbox", name="Lower strings").check()
    _saved(page, first, lambda t: "- [x] Lower strings" in t)
    expect(cell.locator(".md-count")).to_have_text("2/3")
    assert not csp_violations


def test_links_other_cues_and_leaving(
    page: Any, app: str, sign_in_as: SignInAs, csp_violations: list[str]
) -> None:
    first, second, _ = _set_up(page, app, sign_in_as, "Casey Composer")
    _cell(page, first).click()
    panel = page.locator("#side-panel")
    doc = panel.locator(".notes-panel__doc")
    expect(doc).to_be_focused()

    # A link: select words, Ctrl+K, an address. Script links are refused.
    # (After a list, the end of the notes is an empty line for more.)
    _to_end(page)
    page.keyboard.type("Ref mix")
    _saved(page, first, lambda t: t.endswith("Ref mix"))
    _select_back(page, "Ref mix")
    page.keyboard.press("ControlOrMeta+k")
    address = panel.get_by_role("textbox", name="Link Address")
    expect(address).to_be_focused()
    address.fill("javascript:alert(1)")
    address.press("Enter")
    expect(panel.locator(".notes-panel__status")).to_contain_text("https://")
    address.fill("https://example.com/mix")
    address.press("Enter")
    _saved(
        page, first, lambda t: t.endswith("[Ref mix](https://example.com/mix)")
    )

    # Alt+↓ shows the next cue's notes; its markup stays text.
    doc.focus()
    page.keyboard.press("Alt+ArrowDown")
    expect(panel.locator(".side-panel__title")).to_contain_text(second.number)
    expect(doc).to_contain_text("<img src=x onerror=alert(1)>")
    expect(doc.locator("img")).to_have_count(0)
    expect(doc.locator("a")).to_have_count(0)
    expect(doc).to_contain_text("[click](javascript:alert(1))")
    expect(doc.locator("strong")).to_have_text("bold")

    # Esc goes back to the grid, on the cue's notes cell.
    doc.focus()
    page.keyboard.press("Escape")
    expect(_cell(page, second)).to_be_focused()
    panel.get_by_role("button", name="Close Revision Notes").click()
    expect(panel).to_be_hidden()
    assert not csp_violations


def test_pasted_check_boxes_tick(
    page: Any, app: str, sign_in_as: SignInAs, csp_violations: list[str]
) -> None:
    del csp_violations
    _, _, third = _set_up(page, app, sign_in_as, "Casey Composer")
    cell = _cell(page, third)
    # ☐ and ☑ count as a check list in the grid.
    expect(cell.locator(".md-count")).to_have_text("1/2")
    cell.click()
    panel = page.locator("#side-panel")
    expect(panel.locator(".notes-panel__count")).to_have_text("1 of 2 done")
    expect(
        panel.get_by_role("checkbox", name="Stay in that space")
    ).to_be_checked()
    # The list indented under NOTES reads as a list, as in the grid.
    expect(
        panel.locator(".notes-panel__doc ul:not([data-type]) li")
    ).to_have_text("Messy vibes")
    panel.get_by_role("checkbox", name="Make it sadder").check()
    _saved(page, third, lambda t: "- [x] Make it sadder" in t)
    expect(cell.locator(".md-count")).to_have_text("2/2")


def test_viewers_read_notes_without_editing(
    page: Any, app: str, sign_in_as: SignInAs, csp_violations: list[str]
) -> None:
    del csp_violations
    first, _, _ = _set_up(page, app, sign_in_as, "Vic Viewer")
    _cell(page, first).click()
    panel = page.locator("#side-panel")
    doc = panel.locator(".notes-panel__doc")
    expect(doc).to_contain_text("Kalimba out")
    expect(doc).to_have_attribute("contenteditable", "false")
    expect(panel.get_by_role("toolbar")).to_have_count(0)
    expect(panel.get_by_role("checkbox", name="Lower strings")).to_be_disabled()


def test_a_long_note_stays_inside_its_box(
    page: Any, app: str, sign_in_as: SignInAs, csp_violations: list[str]
) -> None:
    del csp_violations
    first, _, _ = _set_up(page, app, sign_in_as, "Casey Composer")
    long_note = "\n".join(f"- [ ] Item {n}" for n in range(1, 61))
    assert first.current_version is not None
    first.current_version.revision_notes = long_note
    first.current_version.save(update_fields=["revision_notes"])
    page.reload()
    expect(page.locator("#cue-grid[data-ready=true]")).to_be_attached()
    _cell(page, first).click()
    panel = page.locator("#side-panel")
    doc = panel.locator(".notes-panel__doc")
    last = doc.locator("li").last
    expect(last).to_contain_text("Item 60")
    # The box goes round all of the text, however long.
    box = doc.bounding_box()
    item = last.bounding_box()
    assert box is not None and item is not None
    assert item["y"] + item["height"] <= box["y"] + box["height"]
    # Scrolled to the end, the toolbar is still in reach.
    last.scroll_into_view_if_needed()
    expect(panel.get_by_role("button", name="Bold")).to_be_in_viewport()
