"""A composer's day, end to end in a real browser.

Sign in, create a project with its first turnover, import the Pro Tools
fixture, edit a status in the grid, add a version, log a new turnover,
see the conform badge, undo, open the cue, export a cue log and, as an
admin, download a backup. axe-core checks every page and dialog on the
way, and no CSP violation may occur.
"""

import csv
import io
import pathlib
import re
from typing import Any

from playwright.sync_api import expect
import pytest

from cues import models
from cues import timecode
from cues.services import long_play
from tests import golden
from tests.large.e2e.conftest import Check
from tests.large.e2e.conftest import SignInAs

# serialized_rollback: keep the views the migrations seed between tests.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

CLIPS = golden.FIXTURES / "protools_clips_23976.txt"


def _segment(scope: Any, name: str) -> None:
    """Chooses a segment: its radio is hidden behind the label."""
    scope.locator(".segmented__choice").filter(
        has=scope.page.get_by_label(name, exact=True)
    ).click()


def _rows(page: Any) -> Any:
    """The cue rows (reel group rows are rows too)."""
    return page.locator(".ag-row:not(.group-row-node)")


def _row(page: Any, number: str) -> Any:
    return _rows(page).filter(has_text=number)


def _create_project(page: Any, axe: Check) -> models.Project:
    page.get_by_role("link", name="New Project").click()
    page.fill("[name=title]", "The Voyage")
    page.fill("[name=production_number]", "TV101")
    page.select_option("[name=frame_rate]", "23.976")
    page.fill("[name=deadlines-composition_end]", "2026-12-04")
    axe("new project")
    page.get_by_role("button", name="Create Project").click()
    expect(page.get_by_role("heading", name="The Voyage")).to_be_visible()
    project = models.Project.objects.get(title="The Voyage")
    assert project.deadlines.get().kind == "composition"
    return project


def _first_turnover(page: Any, app: str, project: models.Project) -> None:
    """Reels come with the first turnover, logged on the Picture page."""
    page.goto(f"{app}/projects/{project.pk}/picture/new/")
    page.fill("[name=label]", "c0917")
    page.fill("[name=received_date]", "2026-09-17")
    page.fill("[name=new-0-name]", "1")
    page.fill("[name=new-0-tc_in]", "1:00:00:00")
    page.fill("[name=new-0-tc_out]", "1:20:00:00")
    page.get_by_role("button", name="Log Turnover").click()
    page.goto(f"{app}/projects/{project.pk}/")


def _tool(page: Any, name: str) -> None:
    """Picks an item from the toolbar's Tools menu."""
    page.locator("#tools-menu > summary").click()
    page.get_by_role("menuitem", name=re.compile(f"^{name}")).click()


def _import_clips(page: Any, axe: Check) -> None:
    _tool(page, "Import Cues")
    dialog = page.locator("#app-dialog")
    expect(dialog.get_by_role("heading", name="Import Cues")).to_be_visible()
    axe("import dialog")
    dialog.locator("input[type=file]").set_input_files(str(CLIPS))
    dialog.get_by_role("button", name="Preview").click()
    expect(dialog.get_by_text("Pro Tools clips:")).to_be_visible()
    axe("import preview")
    dialog.get_by_role("button", name="Import", exact=True).click()
    expect(page.locator("#grid-status")).to_contain_text(
        "Imported protools_clips_23976.txt: 3 new"
    )
    expect(_rows(page)).to_have_count(3)


def _groups_and_selection(page: Any) -> None:
    """Reel rows, the selection summary, invert, collapse, right-click."""
    group = page.locator(".group-row", has_text="Reel 1 – c0917")
    expect(group).to_be_visible()
    for number in ("1m01", "1m02"):
        _row(page, number).locator(
            "[col-id=ag-Grid-SelectionColumn] input"
        ).check()
    summary = page.locator("#grid-selection")
    expect(summary).to_contain_text("2 cues selected")
    page.keyboard.press("Control+Shift+I")
    expect(summary).to_contain_text("1 cue selected")
    summary.get_by_role("button", name="Deselect All").click()
    expect(summary).to_be_hidden()
    reel_check = group.get_by_role("checkbox", name="Select reel 1")
    reel_check.click()
    expect(summary).to_contain_text("3 cues selected")
    expect(group.locator(".ag-checked")).to_have_count(1)
    summary.get_by_role("button", name="Deselect All").click()
    # Find: a magnifier until used; the field closes again when empty.
    page.get_by_role("button", name="Find a cue").click()
    search = page.get_by_role("searchbox", name=re.compile("^Find a cue"))
    search.fill("1m02")
    expect(_rows(page)).to_have_count(1)
    expect(page.locator("#grid-status")).to_have_text("1 of 3 cues shown.")
    search.fill("")
    page.keyboard.press("Escape")
    expect(search).to_be_hidden()
    group.get_by_role("button", name="Reel 1").click()  # collapse
    expect(_rows(page)).to_have_count(0)
    group.get_by_role("button", name="Reel 1").click()
    expect(_rows(page)).to_have_count(3)
    # The timeline: a block per cue; a click finds it in the grid.
    expect(page.locator(".timeline__cue")).to_have_count(3)
    page.locator('.timeline__cue[data-number="1m02"]').click()
    expect(_row(page, "1m02").locator(".ag-cell-focus")).to_have_count(1)
    _row(page, "1m01").locator("[col-id=title]").click(button="right")
    menu = page.get_by_role("menu")
    expect(
        menu.get_by_role("menuitem", name=re.compile("^Copy"))
    ).to_have_count(2)
    page.keyboard.press("Escape")
    expect(menu).to_be_hidden()


def _views(page: Any, axe: Check, project: models.Project) -> None:
    """Duplicate a view, edit its columns in the dialog, delete it."""
    standard = page.locator(".view-tab-wrap", has_text="Standard")
    standard.get_by_role("button", name="Standard view options").click()
    page.get_by_role("menuitem", name="Duplicate").click()
    copy = page.locator(".view-tab", has_text="Standard 2")
    expect(copy).to_have_attribute("aria-pressed", "true")
    wrap = page.locator(".view-tab-wrap", has_text="Standard 2")
    wrap.get_by_role("button", name="Standard 2 view options").click()
    page.get_by_role("menuitem", name="Edit…").click()
    dialog = page.locator("#app-dialog")
    expect(
        dialog.get_by_role("heading", name="Edit View: Standard 2")
    ).to_be_visible()
    axe("view editor")
    dialog.get_by_label("Name").fill("Writing")
    dialog.locator(
        ".view-tile--active", has_text="Thumbnail"
    ).click()  # drop it
    dialog.get_by_role("button", name="Save View").click()
    expect(page.locator(".view-tab", has_text="Writing")).to_be_visible()
    expect(page.locator(".ag-header-cell[col-id=thumbnail]")).to_have_count(0)
    view = project.views.get(name="Writing")
    assert "thumbnail" not in [c["id"] for c in view.columns]
    page.locator(".view-tab-wrap", has_text="Writing").get_by_role(
        "button", name="Writing view options"
    ).click()
    page.get_by_role("menuitem", name="Delete").click()
    # The app's own confirm dialog, not the browser's.
    confirm = page.locator(".confirm-dialog")
    expect(confirm).to_contain_text("Delete the Writing view?")
    axe("confirm dialog", within=".confirm-dialog")
    confirm.get_by_role("button", name="Delete View").click()
    expect(page.locator(".view-tab", has_text="Writing")).to_have_count(0)
    assert not project.views.exists()


def _project_notes(page: Any, axe: Check, project: models.Project) -> None:
    """The notes panel beside the grid: write a note, it saves on leaving."""
    page.locator("#notes-toggle").click()
    panel = page.locator("#side-panel")
    expect(panel.get_by_role("heading", name="Project Notes")).to_be_visible()
    axe("project notes panel")
    general = panel.get_by_role("textbox", name="General")
    general.fill("Director prefers strings.")
    general.press("Tab")  # change: the note saves
    expect(panel.locator("#note-general")).to_contain_text("Saved.")
    assert project.notes.get(kind="general").text == "Director prefers strings."
    panel.get_by_role("button", name="Close project notes").click()
    expect(panel).to_be_hidden()


def _split_and_undo(page: Any, axe: Check, project: models.Project) -> None:
    """Tools: a tool waits for a selection, previews, applies, undoes."""
    page.locator("#tools-menu > summary").click()
    expect(page.get_by_role("menuitem", name="Split Cue")).to_be_disabled()
    page.keyboard.press("Escape")
    _row(page, "1m03").locator("[col-id=ag-Grid-SelectionColumn] input").check()
    _tool(page, "Split Cue")
    dialog = page.locator("#app-dialog")
    expect(dialog.get_by_role("heading", name="Split Cue")).to_be_visible()
    expect(dialog.locator("#tool-preview")).to_contain_text("1m03a is new")
    axe("split dialog")
    dialog.get_by_role("button", name="Split", exact=True).click()
    new_cue = page.locator("[col-id=number]", has_text=re.compile("^1m03a$"))
    expect(new_cue).to_be_visible()
    assert project.cues.filter(number="1m03a").exists()
    _tool(page, "Undo Last Change")
    expect(new_cue).to_have_count(0)
    page.locator("#grid-selection").get_by_role(
        "button", name="Deselect All"
    ).click()


def _stats(page: Any, axe: Check) -> None:
    """The stats panel: a swatch selects, a name filters."""
    # Tooltips: the title becomes a styled box with the shortcut as a key.
    stats_button = page.locator("#stats-toggle")
    stats_button.hover()
    tooltip = page.locator("#app-tooltip")
    mac = page.evaluate(
        "() => /mac/i.test(navigator.userAgentData?.platform"
        " || navigator.platform)"
    )
    keys = "⇧⌘S" if mac else "Ctrl+Shift+S"
    expect(tooltip).to_have_text(f"Stats{keys}")
    expect(tooltip.locator("kbd")).to_have_text(keys)
    assert stats_button.get_attribute("title") is None
    page.mouse.move(0, 0)
    expect(tooltip).to_be_hidden()
    page.keyboard.press("Control+Shift+S")
    panel = page.locator("#side-panel")
    expect(panel.get_by_role("heading", name="Stats")).to_be_visible()
    axe("stats panel")
    # The overview fills the window; the long panel scrolls, not the page.
    assert page.evaluate(
        "document.scrollingElement.scrollHeight <= window.innerHeight"
    )
    assert page.evaluate(
        "document.querySelector('main').offsetWidth === window.innerWidth"
    )
    panel.get_by_role("button", name="Select Total cues").click()
    summary = page.locator("#grid-selection")
    expect(summary).to_contain_text("3 cues selected")
    summary.get_by_role("button", name="Deselect All").click()
    panel.get_by_role("button", name="Compositions", exact=True).click()
    badge = page.locator("#grid-filter-button .toolbar__count")
    expect(badge).to_have_text("1")
    page.keyboard.press("Control+Shift+X")
    expect(badge).to_be_hidden()
    panel.get_by_role("button", name="Close stats").click()
    expect(panel).to_be_hidden()


def _edit_status(page: Any, project: models.Project) -> None:
    before = project.cues.get(number="1m01").status
    # One click opens the status list; typing filters it.
    _row(page, "1m01").locator("[col-id=status]").click()
    editor = page.locator(".status-editor")
    expect(editor).to_be_visible()
    page.keyboard.type("compo")
    editor.get_by_role("option", name="Composed").click()
    expect(page.locator("#grid-status")).to_contain_text("Saved 1m01.")
    assert project.cues.get(number="1m01").status != before


def _batch_edit(page: Any, axe: Check, project: models.Project) -> None:
    """With two cues selected, an edit can go to both, undone as one."""
    for number in ("1m02", "1m03"):
        _row(page, number).locator(
            "[col-id=ag-Grid-SelectionColumn] input"
        ).check()
    _row(page, "1m02").locator("[col-id=status]").click()
    page.keyboard.type("in prog")
    page.locator(".status-editor").get_by_role(
        "option", name="In Progress"
    ).click()
    dialog = page.locator("#app-dialog")
    expect(dialog).to_contain_text("Apply Status = In Progress to 2")
    axe("batch edit question")
    dialog.get_by_role("button", name="Apply to 2 Cues").click()
    expect(page.locator("#grid-status")).to_contain_text("Saved 2 cues.")
    labels = {
        cue.status.label if cue.status else ""
        for cue in project.cues.filter(number__in=["1m02", "1m03"])
    }
    assert labels == {"In Progress"}
    expect(page.locator("#grid-selection")).to_be_hidden()  # deselected
    _tool(page, "Undo Last Change")
    expect(page.locator("#grid-status")).to_contain_text("Undid:")
    assert "In Progress" not in {
        cue.status.label if cue.status else ""
        for cue in project.cues.filter(number__in=["1m02", "1m03"])
    }


def _long_play_time(page: Any, axe: Check, project: models.Project) -> None:
    """The cue log as CSV with the reels on one timeline."""
    dialog = page.locator("#app-dialog")
    leader = dialog.get_by_label("Leader")
    expect(leader).to_be_hidden()
    dialog.get_by_label("Long Play Time").check()
    leader.fill("0:00:08:00")
    axe("export dialog with long play time")
    _segment(dialog, "CSV")
    with page.expect_download() as info:
        dialog.get_by_role("button", name="Export", exact=True).click()
    text = pathlib.Path(info.value.path()).read_text(encoding="utf-8-sig")
    header, *rows = csv.reader(io.StringIO(text))
    found = {row[header.index("Cue #")]: row for row in rows}
    cue = project.cues.get(number="1m01")
    rate = project.rate
    options = long_play.Options(start_hour=1, leader=8 * 24)
    shift = long_play.shifts(
        long_play.reels(project), long_play.start(options, rate)
    )[cue.picture_id]
    assert cue.tc_in is not None
    expected = timecode.format_timecode(cue.tc_in + shift, rate)
    assert found["1m01"][header.index("IN")] == expected
    dialog.get_by_label("Long Play Time").uncheck()
    _segment(dialog, "Excel")


def _export(page: Any, axe: Check, project: models.Project) -> None:
    """The cue log as Excel, then the wall chart as PDF."""
    _tool(page, "Export Cues")
    dialog = page.locator("#app-dialog")
    expect(dialog.get_by_role("heading", name="Export Cues")).to_be_visible()
    axe("export dialog")
    with page.expect_download() as info:
        dialog.get_by_role("button", name="Export", exact=True).click()
    assert re.fullmatch(
        r"TV101 The Voyage Cue Log \d{4}-\d\d-\d\d\.xlsx",
        info.value.suggested_filename,
    )
    _long_play_time(page, axe, project)
    _segment(dialog, "Wall Chart")
    expect(dialog.get_by_label("Choose columns")).to_be_hidden()
    with page.expect_download() as info:
        dialog.get_by_role("button", name="Export", exact=True).click()
    assert re.fullmatch(
        r"TV101 The Voyage Wall Chart \d{4}-\d\d-\d\d\.pdf",
        info.value.suggested_filename,
    )
    # The export dialog stays open; the leader was typed, so Escape asks.
    page.keyboard.press("Escape")
    page.locator(".confirm-dialog").get_by_role(
        "button", name="Discard"
    ).click()
    expect(dialog).to_be_hidden()


def _search(page: Any, axe: Check) -> None:
    """Ctrl+K finds a cue by number and title words and opens it."""
    page.keyboard.press("Control+k")
    palette = page.locator("#palette")
    expect(palette).to_be_visible()
    axe("search palette")
    palette.get_by_role("combobox").fill("1m02 chase")
    expect(palette.get_by_role("option").first).to_contain_text("The Chase")
    page.keyboard.press("Enter")
    expect(
        page.get_by_role("heading", name=re.compile("1m02 The Chase"))
    ).to_be_visible()
    page.go_back()
    expect(page.get_by_role("heading", name="The Voyage")).to_be_visible()


def _cue_sheet(page: Any, axe: Check) -> None:
    """The cue sheet: a checklist that follows the choices, a download."""
    _tool(page, "Export Cue Sheet")
    dialog = page.locator("#app-dialog")
    expect(
        dialog.get_by_role("heading", name="Export Cue Sheet")
    ).to_be_visible()
    checklist = dialog.locator("#cue-sheet-checklist")
    expect(checklist).to_contain_text("Add at least one writer.")
    axe("cue sheet dialog")
    _segment(dialog, "CSV (One Row per Writer or Publisher)")
    with page.expect_download() as info:
        dialog.get_by_role("button", name="Export Cue Sheet").click()
    assert info.value.suggested_filename.endswith(".csv")
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()


def _type_a_new_sub_status(page: Any, project: models.Project) -> None:
    """Typing a name that isn't on the list adds it."""
    _row(page, "1m01").locator("[col-id=sub_status]").click()
    expect(page.locator(".status-editor")).to_be_visible()
    page.keyboard.type("Needs Mix")
    page.get_by_role("option", name='Add "Needs Mix"').click()
    expect(_row(page, "1m01").locator("[col-id=sub_status]")).to_contain_text(
        "Needs Mix"
    )
    assert project.sub_statuses.filter(label="Needs Mix").exists()


def _add_themes(page: Any, project: models.Project) -> None:
    """Theme chips: type to add (new names join the list), time, save."""
    _row(page, "1m01").locator("[col-id=themes]").click()
    editor = page.locator(".chips-editor")
    expect(editor).to_be_visible()
    page.keyboard.type("Aurora")
    page.keyboard.press("Enter")
    editor.get_by_label("Aurora duration").fill("1:20")
    editor.get_by_label("Aurora duration").press("Enter")
    page.keyboard.type("The Sea")
    page.keyboard.press("Enter")
    with page.expect_response(lambda r: r.request.method == "PATCH") as saved:
        page.keyboard.press("Enter")  # empty box: save
    assert saved.value.ok
    cell = _row(page, "1m01").locator("[col-id=themes]")
    expect(cell).to_contain_text("Aurora 1:20")
    expect(cell).to_contain_text("The Sea")
    assert list(project.themes.values_list("name", flat=True)) == [
        "Aurora",
        "The Sea",
    ]


def _add_version(page: Any, axe: Check, project: models.Project) -> None:
    _row(page, "1m01").locator(".version-cell__add").click()
    dialog = page.locator("#app-dialog")
    expect(
        dialog.get_by_role("heading", name=re.compile("New Version of 1m01"))
    ).to_be_visible()
    axe("new version dialog")
    dialog.get_by_role("button", name="Save Version", exact=True).click()
    expect(dialog).to_be_hidden()
    expect(_row(page, "1m01")).to_contain_text("v2")
    cue = project.cues.get(number="1m01")
    assert cue.current_version is not None
    assert cue.current_version.label == "v2"


def _log_turnover(
    page: Any, axe: Check, app: str, project: models.Project
) -> None:
    page.goto(f"{app}/projects/{project.pk}/picture/")
    axe("picture page")
    page.get_by_role("link", name=re.compile("turnover", re.I)).first.click()
    page.fill("[name=label]", "c1030")
    page.fill("[name=received_date]", "2026-10-30")
    page.check("[name=reels-0-include]")
    page.fill("[name=reels-0-tc_in]", "1:00:02:12")
    page.fill("[name=reels-0-tc_out]", "1:20:02:12")
    axe("new turnover")
    page.get_by_role("button", name="Log Turnover").click()
    assert project.turnovers.count() == 2
    # Reel 1's head moved: the conform assistant offers to shift its cues.
    expect(page.get_by_role("heading", name="Conform", level=2)).to_be_visible()
    offer = page.get_by_label(
        re.compile(r"Shift Reel 1's \d+ cues by \+00:00:02:12")
    )
    expect(offer).to_be_checked()
    axe("conform")
    page.get_by_role("button", name="Shift Cues").click()
    expect(page.get_by_text(re.compile(r"Shifted \d+ cues"))).to_be_visible()


def _settings_pages(
    page: Any, axe: Check, app: str, project: models.Project
) -> None:
    """Settings: a page per section, each reached from the sidebar."""
    page.goto(f"{app}/projects/{project.pk}/settings/")
    for label in (
        "General",
        "Picture",
        "Cue Sheet",
        "Statuses",
        "Lists",
        "Departments",
        "Deadlines",
        "Views",
        "Project",
    ):
        page.locator(".settings__nav").get_by_role("link", name=label).click()
        expect(
            page.get_by_role("heading", name=label, exact=True, level=2)
        ).to_be_visible()
        axe(f"settings: {label}")
    page.goto(f"{app}/settings/")
    axe("app settings")
    page.goto(f"{app}/credits/")
    axe("credits")


def test_a_composers_day(  # pylint: disable=too-many-arguments
    page: Any,
    app: str,
    sign_in_as: SignInAs,
    axe: Check,
    csp_violations: list[str],
) -> None:
    del csp_violations  # checked when the test ends
    sign_in_as("Casey Composer")
    axe("overview")
    project = _create_project(page, axe)
    _first_turnover(page, app, project)
    _import_clips(page, axe)
    axe("cue grid")
    _groups_and_selection(page)
    _views(page, axe, project)
    _project_notes(page, axe, project)
    _split_and_undo(page, axe, project)
    _stats(page, axe)
    _edit_status(page, project)
    _batch_edit(page, axe, project)
    _type_a_new_sub_status(page, project)
    _add_themes(page, project)
    _add_version(page, axe, project)

    _log_turnover(page, axe, app, project)
    page.goto(f"{app}/projects/{project.pk}/")
    expect(
        page.get_by_text(re.compile(r"\d+ on older picture"))
    ).to_be_visible()

    _tool(page, "Undo Last Change")
    expect(page.locator("#grid-status")).to_contain_text("Undid:")

    _row(page, "1m02").locator("[col-id=version]").click()
    page.get_by_role("button", name="Details", exact=True).click()
    expect(
        page.get_by_role("heading", name=re.compile("1m02 The Chase"))
    ).to_be_visible()
    axe("cue details")
    page.keyboard.press("Escape")
    expect(page.get_by_role("heading", name="The Voyage")).to_be_visible()

    _settings_pages(page, axe, app, project)

    page.goto(f"{app}/projects/{project.pk}/")
    _export(page, axe, project)
    _search(page, axe)
    _cue_sheet(page, axe)
    _profile_menu(page, axe)
    page.get_by_role("button", name="Sign Out").click()
    sign_in_as("Nick Admin")
    page.get_by_role("link", name="Backup").click()
    axe("backup")
    with page.expect_download() as info:
        page.get_by_role("link", name="Download Backup").click()
    assert info.value.suggested_filename.startswith("reeltime-backup-")


def _profile_menu(page: Any, axe: Check) -> None:
    """The picture from the provider opens the profile menu."""
    button = page.locator(".profile__button")
    picture = button.locator("img.avatar")
    expect(picture).to_be_visible()
    assert picture.evaluate("img => img.naturalWidth") == 96
    button.click()
    menu = page.locator(".profile__panel")
    expect(menu).to_contain_text("Casey Composer")
    expect(menu).to_contain_text("composer@example.test")
    expect(menu).to_contain_text("Editor")
    expect(menu.get_by_role("link", name="Manage Account")).to_be_visible()
    # Only the menu: it covers the timeline, which axe would then read as
    # low-contrast text on the menu's background.
    axe("profile menu", within=".profile")


def test_keyboard_path_through_grid_and_dialogs(
    page: Any,
    app: str,
    sign_in_as: SignInAs,
    csp_violations: list[str],
) -> None:
    """The grid and dialogs work without a mouse."""
    del csp_violations, app
    sign_in_as("Casey Composer")
    page.get_by_role("link", name="New Project").click()
    page.fill("[name=title]", "Keys Only")
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", name="Keys Only")).to_be_visible()
    # Shortcuts start once the grid has its data.
    expect(page.locator("#cue-grid[data-ready=true]")).to_be_attached()

    # Ctrl+N opens New cue with the number selected; Enter saves.
    page.keyboard.press("Control+n")
    dialog = page.locator("#app-dialog")
    expect(dialog.get_by_role("heading", name="New Cue")).to_be_visible()
    expect(dialog.locator("[name=number]")).to_be_focused()
    page.keyboard.type("1m01")
    page.keyboard.press("Tab")
    page.keyboard.type("Typed In")
    page.keyboard.press("Enter")
    expect(dialog).to_be_hidden()
    expect(_rows(page)).to_have_count(1)

    # Tab into the grid (its header first), move with the arrows.
    for _ in range(40):
        page.keyboard.press("Tab")
        if page.evaluate("() => !!document.activeElement.closest('.ag-root')"):
            break
    else:
        pytest.fail("Tab never reached the grid")
    page.keyboard.press("ArrowDown")  # from the header to the reel's row
    page.keyboard.press("ArrowDown")  # to the first cue
    page.keyboard.press("ArrowRight")

    # Ctrl+Shift+V: new version for the focused cue; Escape closes it.
    page.keyboard.press("Control+Shift+V")
    expect(
        dialog.get_by_role("heading", name=re.compile("New Version of 1m01"))
    ).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()

    # Ctrl+Enter opens the cue; Escape comes back. The heading can show
    # before the page's scripts run, so wait for them first.
    page.keyboard.press("Control+Enter")
    expect(
        page.get_by_role("heading", name=re.compile("1m01 Typed In"))
    ).to_be_visible()
    page.wait_for_load_state("load")
    page.keyboard.press("Escape")
    expect(page.get_by_role("heading", name="Keys Only")).to_be_visible()


@pytest.mark.parametrize(
    ("platform", "search", "new_cue", "import_cues"),
    [
        ("Win32", "Ctrl+K", "Ctrl+N", "Ctrl+I"),
        ("MacIntel", "⌘K", "⌃N", "⌘I"),
    ],
)
def test_keyboard_hints_follow_the_computer(  # pylint: disable=too-many-arguments
    page: Any,
    sign_in_as: SignInAs,
    csp_violations: list[str],
    platform: str,
    search: str,
    new_cue: str,
    import_cues: str,
) -> None:
    """Hints read ⌘ on a Mac and Ctrl elsewhere; ⌘N stays the browser's."""
    del csp_violations
    page.add_init_script(
        "Object.defineProperty(navigator, 'platform', {get: () => '"
        + platform
        + "'});"
        "Object.defineProperty(navigator, 'userAgentData',"
        " {get: () => undefined});"
    )
    sign_in_as("Casey Composer")
    expect(page.locator(".topbar__search kbd")).to_have_text(search)
    page.get_by_role("link", name="New Project").click()
    page.fill("[name=title]", "Hints")
    page.keyboard.press("Enter")
    expect(page.locator("#grid-new-cue")).to_have_attribute(
        "title", f"New cue ({new_cue})"
    )
    expect(page.locator("#grid-import kbd")).to_have_text(import_cues)


def test_days_off_calendar(
    page: Any, sign_in_as: SignInAs, axe: Check, csp_violations: list[str]
) -> None:
    """Clicking calendar days adds days off; Shift-click adds a range."""
    del csp_violations
    sign_in_as("Casey Composer")
    page.get_by_role("link", name="New Project").click()
    page.fill("[name=title]", "Holidays")
    page.locator(".days-off > summary").click()
    calendar = page.locator("[data-calendar]")
    expect(calendar).to_be_visible()
    axe("days off calendar")
    days = calendar.locator(".calendar__day")
    days.nth(9).click()
    days.nth(11).click(modifiers=["Shift"])
    expect(calendar.locator("[aria-pressed=true]")).to_have_count(3)
    days.nth(10).click()  # off again
    expect(calendar.locator("[aria-pressed=true]")).to_have_count(2)
    # Today brings the calendar back from other months.
    titles = calendar.locator(".calendar__title")
    first_month = titles.first.inner_text()
    calendar.get_by_role("button", name="Next month").click()
    calendar.get_by_role("button", name="Next month").click()
    expect(titles.first).not_to_have_text(first_month)
    calendar.get_by_role("button", name="Today").click()
    expect(titles.first).to_have_text(first_month)
    # Date fields open the app's own calendar, not the browser's.
    end = page.locator("[name=deadlines-composition_end]")
    end.locator("xpath=..").locator(".date-field__button").click()
    picker = page.locator(".date-picker")
    expect(picker).to_be_visible()
    axe("date picker")
    picker.get_by_role("button", name="Today").click()
    expect(picker).to_be_hidden()
    assert end.input_value()
    page.get_by_role("button", name="Create Project").click()
    expect(page.get_by_role("heading", name="Holidays")).to_be_visible()
    assert models.DayOff.objects.count() == 2

    # The empty grid says how to add cues, with buttons that do it.
    dialog = page.locator("#app-dialog")
    page.locator(".grid-empty").get_by_role(
        "button", name="Import Cues"
    ).click()
    expect(dialog.get_by_role("heading", name="Import Cues")).to_be_visible()
    page.keyboard.press("Escape")
    page.locator("#grid-import-open").click()
    expect(dialog.get_by_role("heading", name="Import Cues")).to_be_visible()


def test_projects_bulk_archive_and_show_archived(
    page: Any, sign_in_as: SignInAs, csp_violations: list[str]
) -> None:
    """Tick a project, archive it; Show Archived brings it back at once."""
    del csp_violations
    sign_in_as("Casey Composer")
    page.get_by_role("link", name="New Project").click()
    page.fill("[name=title]", "Old Film")
    page.get_by_role("button", name="Create Project").click()
    page.get_by_role("link", name="Projects").first.click()
    page.get_by_label("Select Old Film").check()
    expect(page.locator("[data-bulk-count]")).to_contain_text("1 selected")
    page.get_by_role("button", name="Archive", exact=True).click()
    expect(page.get_by_text("Archived 1 project.")).to_be_visible()
    expect(page.get_by_role("link", name="Old Film")).to_have_count(0)
    page.get_by_label("Show Archived").check()  # no Search press needed
    expect(page.get_by_role("link", name="Old Film")).to_be_visible()


def test_credits_add_find_and_remove(
    page: Any,
    app: str,
    sign_in_as: SignInAs,
    axe: Check,
    csp_violations: list[str],
) -> None:
    """Add a writer and a publisher, find one, remove one."""
    del csp_violations
    sign_in_as("Casey Composer")
    page.goto(f"{app}/credits/")
    axe("credits")
    page.get_by_role("button", name="Add Writer").click()
    page.get_by_label("First name 1").fill("Elena")
    page.get_by_label("Last name 1").fill("Marsh")
    page.get_by_label("PRO 1").first.fill("BMI")
    page.get_by_role("button", name="Add Publisher").click()
    page.get_by_label("Company name 1").fill("Marsh Harbor Music")
    page.get_by_role("button", name="Save").click()
    expect(page.get_by_text("Saved writers and publishers.")).to_be_visible()
    writer = models.Party.objects.get(last_name="Marsh")
    assert (writer.name, writer.society) == ("Elena Marsh", "BMI")
    assert models.Party.objects.filter(name="Marsh Harbor Music").exists()

    page.get_by_placeholder("Find a writer or publisher").fill("harbor")
    expect(page.get_by_label("First name 1")).to_be_hidden()
    expect(page.get_by_label("Company name 1")).to_be_visible()
    page.get_by_placeholder("Find a writer or publisher").fill("")
    page.locator("[data-prefix=publishers]").get_by_role(
        "button", name="Remove this row"
    ).first.click()
    page.get_by_role("button", name="Save").click()
    expect(page.get_by_text("Saved writers and publishers.")).to_be_visible()
    assert not models.Party.objects.filter(name="Marsh Harbor Music").exists()
