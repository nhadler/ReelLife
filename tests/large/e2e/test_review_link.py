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
AUDIO = pathlib.Path(__file__).parent / "data" / "cue.wav"
AUDIO_LINK = (
    "https://www.dropbox.com/scl/fi/def456/1m01_v1_2mx.wav?rlkey=j&dl=0"
)
FILE_LINK = "https://www.dropbox.com/scl/fi/abc123/1m01_v1.mp4?rlkey=k&dl=0"


def _dropbox(page: Any) -> list[str]:
    """Answers Dropbox's address: a three-second test video, or audio."""
    asked: list[str] = []

    def answer(route: Any) -> None:
        asked.append(route.request.url)
        audio = ".wav" in route.request.url
        data = (AUDIO if audio else BOUNCE).read_bytes()
        kind = "audio/wav" if audio else "video/webm"
        # Seeking asks for byte ranges, as Dropbox's file servers allow.
        wanted = re.fullmatch(
            r"bytes=(\d+)-(\d*)", route.request.headers.get("range", "")
        )
        if wanted is None:
            route.fulfill(
                body=data,
                content_type=kind,
                headers={"Accept-Ranges": "bytes"},
            )
            return
        first = int(wanted[1])
        last = int(wanted[2]) if wanted[2] else len(data) - 1
        route.fulfill(
            status=206,
            body=data[first : last + 1],
            content_type=kind,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Range": f"bytes {first}-{last}/{len(data)}",
            },
        )

    page.route("https://www.dropbox.com/**", answer)
    return asked


def _watch(page: Any, url: str, name: str = "Dana Director") -> None:
    """Opens a review link as a guest: says who's watching, then watches."""
    page.goto(url)
    page.get_by_label("Your Name").fill(name)
    page.get_by_role("button", name="Watch").click()
    _ready(page)


def _ready(page: Any) -> None:
    """Waits until the review page's scripts have run.

    A test types faster than anyone could: before then Enter would only
    break the line, and Send would post the form the plain way.
    """
    page.wait_for_url("**/review/*/")
    page.wait_for_load_state("load")
    expect(page.locator("#review-time")).not_to_be_empty()


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

    _watch(page, f"{app}/review/{link.token}/")
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

    # A comment at the frame on screen, under the name given; Enter sends.
    expect(page.get_by_text("Commenting as Dana Director")).to_be_visible()
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

    # Unticked, a comment is about the whole cue.
    page.locator("#review-timed").uncheck()
    box.fill("Love the theme")
    page.get_by_role("button", name="Send").click()
    expect(listed).to_have_count(2)
    expect(listed.first).to_contain_text("Overall")
    # The choice is kept for the next comment; tick it again.
    expect(page.locator("#review-timed")).not_to_be_checked()
    page.locator("#review-timed").check()
    assert models.ReviewComment.objects.filter(frame=None).count() == 1

    # A comment's timecode goes to its frame.
    forward.click()
    expect(readout).to_have_text(tc(12))
    page.get_by_role("button", name=f"Go to {tc(11)}").click()
    expect(readout).to_have_text(tc(11))
    axe("review page with comments")


def test_the_composer_sets_up_a_link_on_the_cue_page(
    page: Any,
    app: str,
    sign_in_as: SignInAs,
    axe: Check,
    csp_violations: list[str],
) -> None:
    del csp_violations
    cue = demo.build_demo().project.cues.get(number="1m01")
    sign_in_as("Casey Composer")
    page.goto(f"{app}/cues/{cue.pk}/")
    section = page.locator("#cue-review")
    # File Link is in Review; the section says whether it plays.
    expect(section).to_contain_text("Paste a Dropbox link")
    file_link = page.get_by_label("File Link")
    file_link.fill(FILE_LINK)
    file_link.press("Tab")
    expect(section).to_contain_text("Plays on the review page.")
    section.get_by_role("button", name="Make a Review Link").click()
    field = section.get_by_label("Review link")
    expect(field).to_be_visible()
    # The page's "Saved" line is about the cue's fields, not the link.
    expect(page.locator("#cue-status")).not_to_have_text("Not saved.")
    link = models.ReviewLink.objects.get(cue=cue)
    expect(field).to_have_value(f"{app}/review/{link.token}/")
    expect(section).to_contain_text("No password")
    axe("review section", within="#group-review")

    # The settings are fields here, saved as they change.
    section.get_by_label("Password").fill("strings42")
    section.get_by_label("Pre-Roll").click()
    expect(section).to_contain_text("Password set")
    section.get_by_label("Pre-Roll").fill("0:02")
    section.get_by_label("Allow Comments").uncheck()
    expect(section).to_contain_text("Comments closed")
    link.refresh_from_db()
    assert link.preroll == 2 * cue.project.rate.nominal
    assert review.check_password(link, "strings42")
    assert not link.comments_open
    section.get_by_role("button", name="Remove Password").click()
    expect(section).to_contain_text("No password")

    # Comments are counted here and read on the review page.
    version = cue.versions.get()
    review.add_comment(
        version, name="Dana", body="Too busy", frame=None, user=None
    )
    page.reload()
    expect(section).to_contain_text("1 comment on v1, 1 open")
    expect(section).not_to_contain_text("Too busy")

    section.get_by_role("button", name="Revoke Link").click()
    confirm = page.locator(".confirm-dialog")
    expect(confirm).to_contain_text("Revoke this link?")
    confirm.get_by_role("button", name="Revoke Link").click()
    expect(
        section.get_by_role("button", name="Make a Review Link")
    ).to_be_visible()
    assert not models.ReviewLink.objects.exists()


def test_replies_and_an_editor_resolving_on_the_review_page(
    page: Any,
    app: str,
    sign_in_as: SignInAs,
    axe: Check,
    csp_violations: list[str],
) -> None:
    del csp_violations
    cue = _cue_with_video()
    link = review.create(cue, None)
    version = cue.versions.get()
    note = review.add_comment(
        version, name="Dana", body="Too busy", frame=24, user=None
    )
    _dropbox(page)
    sign_in_as("Casey Composer")
    page.goto(f"{app}/review/{link.token}/")
    _ready(page)
    listed = page.locator(".review-comment")
    expect(listed).to_contain_text("Too busy")
    # Reply turns the comment box into a reply, under the user's own name.
    listed.get_by_role("button", name="Reply to Dana").click()
    expect(page.locator("#review-replying")).to_contain_text("Replying to Dana")
    expect(page.locator("#review-timed")).to_be_hidden()
    box = page.get_by_label("Comment", exact=True)
    expect(box).to_be_focused()
    box.fill("Thinned out in v2")
    box.press("Enter")
    expect(listed.locator(".review-reply")).to_contain_text("Thinned out in v2")
    expect(page.locator("#review-replying")).to_be_hidden()
    expect(page.locator("#review-timed")).to_be_visible()
    reply = models.ReviewComment.objects.get(parent=note)
    assert reply.member and reply.frame is None
    axe("review page with a reply")

    # Resolving marks it here and on the scrubber; Reopen undoes it.
    listed.get_by_role("button", name="Resolve the comment by Dana").click()
    expect(listed).to_contain_text("Resolved")
    axe("review page with a resolved comment")
    expect(page.locator(".review-scrub__mark.is-resolved")).to_have_count(1)
    note.refresh_from_db()
    assert note.resolved_at is not None
    listed.get_by_role("button", name="Reopen the comment by Dana").click()
    expect(listed).not_to_contain_text("Resolved")

    # Deleting the note takes its reply; it asks first.
    listed.get_by_role("button", name="Delete the comment by Dana").click()
    confirm = page.locator(".confirm-dialog")
    expect(confirm).to_contain_text("and its 1 reply")
    confirm.get_by_role("button", name="Delete").click()
    expect(listed).to_have_count(0)
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
    page.get_by_label("Your Name").fill("Dana")
    page.get_by_role("button", name="Watch").click()
    _ready(page)
    expect(page.get_by_role("heading", name="1m01")).to_be_visible()
    # The page says the video can't play, and comments still work.
    expect(page.get_by_role("alert")).to_contain_text(
        "This video can't be played in this browser."
    )
    expect(page.get_by_role("link", name="Open the File")).to_be_visible()
    page.get_by_label("Comment", exact=True).fill("Can't see it")
    page.get_by_role("button", name="Send").click()
    expect(page.locator(".review-comment")).to_contain_text("Can't see it")
    # With nothing on screen, the comment is on the whole cue.
    expect(page.locator("#review-timed")).to_be_hidden()
    assert models.ReviewComment.objects.get().frame is None
    axe("review page without video")


def test_the_audio_alone_plays_under_the_video(
    page: Any, app: str, axe: Check, csp_violations: list[str]
) -> None:
    del csp_violations
    cue = _cue_with_video()
    cue.versions.update(audio_link=AUDIO_LINK)
    link = review.create(cue, None)
    asked = _dropbox(page)
    _watch(page, f"{app}/review/{link.token}/")
    player = page.locator("#review-audio")
    expect(player).to_contain_text("Audio Only")
    expect(player.locator("#review-audio-time")).to_have_text("0:00 / 0:03")
    assert any("1m01_v1_2mx.wav" in url and "raw=1" in url for url in asked)
    axe("review page with audio")
    # One at a time: starting the audio pauses the video, and back.
    page.get_by_role("button", name="Play", exact=True).click()
    expect(page.locator("#review-player.is-playing")).to_have_count(1)
    player.get_by_role("button", name="Play the audio").click()
    expect(player.get_by_role("button", name="Pause the audio")).to_be_visible()
    expect(page.locator("#review-player.is-playing")).to_have_count(0)
    # Listening to the audio, a comment is about the whole cue.
    expect(page.locator("#review-timed")).to_be_hidden()
    page.get_by_role("button", name="Play", exact=True).click()
    expect(player.get_by_role("button", name="Play the audio")).to_be_visible()
    expect(page.locator("#review-timed")).to_be_visible()
    # A version without an Audio Link has no audio player.
    cue.versions.update(audio_link="")
    page.reload()
    expect(page.locator("#review-audio")).to_have_count(0)


def test_keys_work_after_clicking_the_controls(
    page: Any, app: str, csp_violations: list[str]
) -> None:
    del csp_violations
    cue = _cue_with_video()
    link = review.create(cue, None)
    _dropbox(page)
    start = cue.tc_in
    assert start is not None

    def tc(frames: int) -> str:
        return timecode.format_timecode(start + frames, cue.project.rate)

    _watch(page, f"{app}/review/{link.token}/")
    readout = page.locator("#review-time")
    expect(page.locator("#review-seek")).not_to_have_attribute("max", "0")
    # A click leaves the keyboard with the page, not the button.
    page.get_by_role("button", name="Forward one frame").click()
    expect(readout).to_have_text(tc(1))
    page.keyboard.press("ArrowRight")
    expect(readout).to_have_text(tc(2))
    page.keyboard.press("ArrowLeft")
    expect(readout).to_have_text(tc(1))
    # Space plays whatever was clicked last, and letters whatever the case.
    page.get_by_role("button", name="Mute").click()
    page.keyboard.press("Space")
    expect(page.locator("#review-player.is-playing")).to_have_count(1)
    page.keyboard.press("K")
    expect(page.locator("#review-player.is-playing")).to_have_count(0)
    page.keyboard.press("m")
    expect(page.locator("#review-player.is-muted")).to_have_count(0)
    # With the scrubber focused its own arrows step, and Space still plays.
    page.locator("#review-seek").focus()
    before = readout.inner_text()
    page.keyboard.press("ArrowRight")
    expect(readout).not_to_have_text(before)
    page.keyboard.press("Space")
    expect(page.locator("#review-player.is-playing")).to_have_count(1)
    page.keyboard.press("Space")
    # Typing a comment never drives the player.
    page.get_by_label("Comment", exact=True).fill("k j l m f")
    page.get_by_label("Comment", exact=True).press("Space")
    expect(page.locator("#review-player.is-playing")).to_have_count(0)


def test_the_page_fits_a_phone(
    page: Any, app: str, csp_violations: list[str]
) -> None:
    del csp_violations
    cue = _cue_with_video()
    link = review.create(cue, None)
    review.add_comment(
        cue.versions.get(),
        name="W" * 60,  # a name with nowhere to break
        body="x" * 200,
        frame=10,
        user=None,
    )
    _dropbox(page)
    page.set_viewport_size({"width": 375, "height": 700})
    _watch(page, f"{app}/review/{link.token}/")
    expect(page.locator("#review-seek")).not_to_have_attribute("max", "0")
    # Nothing is wider than the screen, and every control is on it.
    assert page.evaluate("document.documentElement.scrollWidth") <= 375
    for name in ("Play", "Back one frame", "Mute", "Full screen"):
        box = page.get_by_role("button", name=name, exact=True).bounding_box()
        assert box is not None
        assert box["x"] >= 0 and box["x"] + box["width"] <= 375, name


def test_a_comment_that_cannot_be_sent_says_so(
    page: Any, app: str, csp_violations: list[str]
) -> None:
    del csp_violations
    cue = _cue_with_video()
    link = review.create(cue, None)
    _dropbox(page)
    _watch(page, f"{app}/review/{link.token}/", "Dana")
    box = page.get_by_label("Comment", exact=True)
    box.fill("Too busy")
    review.revoke(link)  # while the page is open
    box.press("Enter")
    expect(page.locator("#review-form-error")).to_have_text(
        "This link no longer works. Reload the page."
    )
    # What was typed is still there.
    expect(box).to_have_value("Too busy")


def test_a_guest_says_who_is_watching_first(
    page: Any, app: str, axe: Check, csp_violations: list[str]
) -> None:
    del csp_violations
    link = review.create(_cue_with_video(), None)
    _dropbox(page)
    page.goto(f"{app}/review/{link.token}/")
    # Nothing of the cue shows until the guest gives a name.
    expect(page.get_by_role("heading", name="Who's Watching?")).to_be_visible()
    expect(page.locator("video")).to_have_count(0)
    expect(page.get_by_label("Your Name")).to_be_focused()
    axe("review name")
    page.get_by_label("Your Name").fill("Dana Director")
    page.get_by_role("button", name="Watch").click()
    expect(page.get_by_role("heading", name="1m01")).to_be_visible()
    expect(page.get_by_text("Commenting as Dana Director")).to_be_visible()


def test_watching_is_recorded(
    page: Any, app: str, csp_violations: list[str]
) -> None:
    del csp_violations
    cue = _cue_with_video()
    link = review.create(cue, None)
    _dropbox(page)
    _watch(page, f"{app}/review/{link.token}/", "Dana Director")
    expect(page.locator("#review-seek")).not_to_have_attribute("max", "0")
    visit = models.ReviewView.objects.get()
    assert (visit.name, visit.played, visit.furthest) == (
        "Dana Director",
        0,
        None,
    )
    # Play a little, then leave: the page reports on its way out.
    page.get_by_role("button", name="Play", exact=True).click()
    page.wait_for_timeout(1500)
    page.goto(f"{app}/auth/login/")
    for _ in range(50):
        visit.refresh_from_db()
        if visit.played:
            break
        page.wait_for_timeout(100)
    assert visit.played >= 1
    assert visit.furthest is not None and visit.furthest >= 12
