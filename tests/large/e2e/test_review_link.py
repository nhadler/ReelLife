"""A review link, as a guest uses it: watch, step frames, comment."""

import pathlib
import re
from typing import Any

from playwright.sync_api import expect
import pytest

from cues import models
from cues import timecode
from cues.services import demo
from cues.services import review
from tests.large.e2e.conftest import Check
from tests.large.e2e.conftest import SignInAs

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

BOUNCE = pathlib.Path(__file__).parent / "data" / "bounce.webm"
FILE_LINK = "https://www.dropbox.com/scl/fi/abc123/1m01_v1.mp4?rlkey=k&dl=0"


def _dropbox(page: Any) -> list[str]:
    """Answers Dropbox's address with a three-second test video."""
    asked: list[str] = []

    data = BOUNCE.read_bytes()

    def answer(route: Any) -> None:
        asked.append(route.request.url)
        # Seeking asks for byte ranges, as Dropbox's file servers allow.
        wanted = re.fullmatch(
            r"bytes=(\d+)-(\d*)", route.request.headers.get("range", "")
        )
        if wanted is None:
            route.fulfill(
                body=data,
                content_type="video/webm",
                headers={"Accept-Ranges": "bytes"},
            )
            return
        first = int(wanted[1])
        last = int(wanted[2]) if wanted[2] else len(data) - 1
        route.fulfill(
            status=206,
            body=data[first : last + 1],
            content_type="video/webm",
            headers={
                "Accept-Ranges": "bytes",
                "Content-Range": f"bytes {first}-{last}/{len(data)}",
            },
        )

    page.route("https://www.dropbox.com/**", answer)
    return asked


def _cue_with_video() -> models.Cue:
    cue = demo.build_demo().project.cues.get(number="1m01")
    version = cue.current_version
    assert version is not None
    version.file_link = FILE_LINK
    version.save()
    return cue


def test_a_guest_watches_and_comments(
    page: Any, app: str, axe: Check, csp_violations: list[str]
) -> None:
    del csp_violations
    cue = _cue_with_video()
    link = review.create(cue, None)
    asked = _dropbox(page)
    rate = cue.project.rate
    start = cue.tc_in
    assert start is not None

    def tc(frames: int) -> str:
        return timecode.format_timecode(start + frames, rate)

    page.goto(f"{app}/review/{link.token}/")
    expect(page.get_by_role("heading", name="1m01")).to_be_visible()
    # No way into the app from here.
    expect(page.locator(".topbar")).to_have_count(0)
    readout = page.locator("#review-time")
    expect(readout).to_have_text(tc(0))
    # The file itself is asked for, not Dropbox's page about it.
    assert asked and "raw=1" in asked[0] and "dl=0" not in asked[0]
    expect(page.locator("#review-seek")).not_to_have_attribute("max", "0")
    axe("review page")

    forward = page.get_by_role("button", name="Forward one frame")
    for _ in range(12):
        forward.click()
    expect(readout).to_have_text(tc(12))
    page.get_by_role("button", name="Back one frame").click()
    expect(readout).to_have_text(tc(11))

    # A comment at the frame on screen; Enter sends it.
    page.get_by_label("Your Name").fill("Dana Director")
    box = page.get_by_label("Comment", exact=True)
    box.fill("Hold the strings back here")
    expect(page.locator("#review-at")).to_have_text(tc(11))
    box.press("Enter")
    listed = page.locator(".review-comment")
    expect(listed).to_have_count(1)
    expect(listed).to_contain_text("Dana Director")
    expect(listed).to_contain_text(tc(11))
    expect(box).to_have_value("")
    comment = models.ReviewComment.objects.get()
    assert (comment.frame, comment.name) == (11, "Dana Director")
    assert not comment.member
    expect(page.locator(".review-scrub__mark")).to_have_count(1)
    # The name is asked once.
    expect(page.get_by_text("Commenting as Dana Director")).to_be_visible()

    # Unticked, a comment is about the whole cue.
    page.locator("#review-timed").uncheck()
    box.fill("Love the theme")
    page.get_by_role("button", name="Send").click()
    expect(listed).to_have_count(2)
    expect(listed.first).to_contain_text("Overall")
    expect(page.locator("#review-timed")).to_be_checked()  # the usual kind
    assert models.ReviewComment.objects.filter(frame=None).count() == 1

    # A comment's timecode goes to its frame.
    forward.click()
    expect(readout).to_have_text(tc(12))
    page.get_by_role("button", name=f"Go to {tc(11)}").click()
    expect(readout).to_have_text(tc(11))
    axe("review page with comments")


def test_the_composer_makes_a_link_and_reads_comments(
    page: Any,
    app: str,
    sign_in_as: SignInAs,
    axe: Check,
    csp_violations: list[str],
) -> None:
    del csp_violations
    cue = _cue_with_video()
    sign_in_as("Casey Composer")
    page.goto(f"{app}/cues/{cue.pk}/")
    section = page.locator("#cue-review")
    section.get_by_role("button", name="Make a Review Link").click()
    field = section.get_by_label("Review link")
    expect(field).to_be_visible()
    link = models.ReviewLink.objects.get(cue=cue)
    expect(field).to_have_value(f"{app}/review/{link.token}/")
    expect(section).to_contain_text("No password")
    axe("review section", within="#cue-review")

    # Settings: a password and a pre-roll.
    section.get_by_role("button", name="Settings").click()
    dialog = page.locator("#app-dialog")
    dialog.get_by_label("Password").fill("strings42")
    dialog.get_by_label("Pre-Roll").fill("0:02")
    axe("review link settings", within="#app-dialog")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_be_hidden()
    expect(section).to_contain_text("Password set")
    link.refresh_from_db()
    assert link.preroll == 2 * cue.project.rate.nominal
    assert review.check_password(link, "strings42")

    # A guest's comment shows here; deleting it asks first.
    version = cue.current_version
    assert version is not None
    review.add_comment(
        version, name="Dana", body="Too busy", frame=None, user=None
    )
    page.reload()
    expect(section.locator(".review-comment")).to_contain_text("Too busy")
    section.get_by_role("button", name="Delete the comment by Dana").click()
    confirm = page.locator(".confirm-dialog")
    expect(confirm).to_contain_text("Delete this comment by Dana?")
    confirm.get_by_role("button", name="Delete").click()
    expect(section.locator(".review-comment")).to_have_count(0)
    assert not models.ReviewComment.objects.exists()


def test_a_password_then_a_video_that_cannot_play(
    page: Any, app: str, axe: Check, csp_violations: list[str]
) -> None:
    del csp_violations
    link = review.create(_cue_with_video(), None)
    review.set_password(link, "strings42")
    link.save()
    # Dropbox has nothing at the link any more.
    page.route("https://www.dropbox.com/**", lambda route: route.abort())
    page.goto(f"{app}/review/{link.token}/")
    expect(
        page.get_by_role("heading", name="Enter the Password")
    ).to_be_visible()
    expect(page.locator("video")).to_have_count(0)
    axe("review password")
    page.get_by_label("Password").fill("strings43")
    page.get_by_role("button", name="Open").click()
    expect(page.get_by_text("That isn't the password")).to_be_visible()
    page.get_by_label("Password").fill("strings42")
    page.get_by_role("button", name="Open").click()
    expect(page.get_by_role("heading", name="1m01")).to_be_visible()
    # The page says the video can't play, and comments still work.
    expect(page.get_by_role("alert")).to_contain_text(
        "This video can't be played here."
    )
    expect(page.get_by_role("link", name="Open the File")).to_be_visible()
    page.get_by_label("Your Name").fill("Dana")
    page.get_by_label("Comment", exact=True).fill("Can't see it")
    page.get_by_role("button", name="Send").click()
    expect(page.locator(".review-comment")).to_contain_text("Can't see it")
    # With nothing on screen, the comment is on the whole cue.
    expect(page.locator("#review-timed")).to_be_disabled()
    assert models.ReviewComment.objects.get().frame is None
    axe("review page without video")
