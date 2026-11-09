"""Cue notes in the panel beside the grid: Markdown, check lists, saving."""

from typing import Any

from playwright.sync_api import expect
import pytest

from cues import models
from cues.services import demo
from tests.large.e2e.conftest import Check
from tests.large.e2e.conftest import SignInAs

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

FIRST = "## Spotting\n- [ ] Lower strings\n- [x] Kalimba out"
# Markup and a script link must show as text, never run.
HOSTILE = "<img src=x onerror=alert(1)> [click](javascript:alert(1)) **bold**"


def _notes(cue: models.Cue) -> str:
    cue.refresh_from_db()
    assert cue.current_version is not None
    return cue.current_version.revision_notes


def _set_up(page: Any, app: str, sign_in_as: SignInAs, who: str) -> list[Any]:
    project = demo.build_demo().project
    first, second = project.cues.order_by("number")[:2]
    for cue, text in ((first, FIRST), (second, HOSTILE)):
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
    return [first, second]


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


def test_writing_notes_in_the_panel(
    page: Any,
    app: str,
    sign_in_as: SignInAs,
    axe: Check,
    csp_violations: list[str],
) -> None:
    first, second = _set_up(page, app, sign_in_as, "Casey Composer")
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
    box = panel.locator(".notes-panel__text")
    expect(box).to_be_focused()
    expect(box).to_have_value(FIRST)
    expect(panel.locator(".notes-panel__count")).to_have_text("1 of 2 done")
    axe("cue notes panel", within="#side-panel")

    # Enter in a check list starts the next item; typing saves.
    page.keyboard.press("ControlOrMeta+End")
    page.keyboard.press("Enter")
    page.keyboard.type("Brass swell")
    _saved(
        page,
        first,
        lambda t: t.endswith("- [x] Kalimba out\n- [ ] Brass swell"),
    )
    expect(panel.locator(".notes-panel__count")).to_have_text("1 of 3 done")

    # The toolbar wraps the selection.
    for _ in "Brass swell":
        page.keyboard.press("Shift+ArrowLeft")
    panel.get_by_role("button", name="Bold").click()
    _saved(page, first, lambda t: t.endswith("- [ ] **Brass swell**"))

    # Ticking a box in the preview saves it.
    panel.get_by_role("button", name="Preview").click()
    panel.get_by_role("checkbox", name="Lower strings").check()
    _saved(page, first, lambda t: "- [x] Lower strings" in t)
    expect(cell.locator(".md-count")).to_have_text("2/3")
    expect(panel.locator(".notes-panel__preview strong")).to_have_text(
        "Brass swell"
    )

    # Alt+↓ shows the next cue's notes; its markup stays text.
    panel.get_by_role("button", name="Write").click()
    box.focus()
    page.keyboard.press("Alt+ArrowDown")
    expect(panel.locator(".side-panel__title")).to_contain_text(second.number)
    expect(box).to_have_value(HOSTILE)
    panel.get_by_role("button", name="Preview").click()
    preview = panel.locator(".notes-panel__preview")
    expect(preview).to_contain_text("<img src=x onerror=alert(1)>")
    expect(preview.locator("img")).to_have_count(0)
    expect(preview.locator("a")).to_have_count(0)
    expect(preview.locator("strong")).to_have_text("bold")

    # Esc goes back to the grid, on the cue's notes cell.
    panel.get_by_role("button", name="Write").click()
    box.focus()
    page.keyboard.press("Escape")
    expect(_cell(page, second)).to_be_focused()
    panel.get_by_role("button", name="Close Revision Notes").click()
    expect(panel).to_be_hidden()
    assert not csp_violations


def test_viewers_read_notes_without_editing(
    page: Any, app: str, sign_in_as: SignInAs, csp_violations: list[str]
) -> None:
    del csp_violations
    first, _ = _set_up(page, app, sign_in_as, "Vic Viewer")
    _cell(page, first).click()
    panel = page.locator("#side-panel")
    expect(panel.locator(".notes-panel__preview")).to_contain_text(
        "Kalimba out"
    )
    expect(panel.locator(".notes-panel__text")).to_have_count(0)
    expect(panel.get_by_role("checkbox", name="Lower strings")).to_be_disabled()
