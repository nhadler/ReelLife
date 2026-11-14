"""Review links on the cue page: the link, its settings, the audio."""

from collections.abc import Callable
import datetime

from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from accounts.models import User
from cues import models
from cues import timecode
from cues.services import review
from cues.views import review as review_views
from tests import factories
from tests.medium.review.setup import HX
from tests.medium.review.setup import page_url
from tests.medium.review.setup import panel_url

pytestmark = pytest.mark.django_db


# The cue page's side.


def test_the_cue_page_has_the_file_link_the_link_and_a_count(
    role_client: Callable[[Role], Client],
    cue: models.Cue,
    link: models.ReviewLink,
) -> None:
    version = cue.versions.get()
    note = review.add_comment(
        version, name="Dana", body="Too busy", frame=24, user=None
    )
    review.add_comment(
        version, name="Dana", body="Lovely", frame=None, user=None
    )
    review.add_comment(
        version, name="Casey", body="Fixed", frame=None, user=None, parent=note
    )
    review.resolve(note, None)
    editor = role_client(Role.EDITOR)
    page = editor.get(reverse("cues:cue", args=[cue.pk])).content.decode()
    # File Link sits in Review, with whether it plays.
    review_at = page.index('id="group-review"')
    assert page.index('name="version-file_link"') > review_at
    assert "Plays on the review page." in page and "Open the Bounce" in page
    assert f"http://testserver/review/{link.token}/" in page
    assert "No password" in page and "Not opened yet" in page
    # Notes are counted (replies aren't); they're read on the review page.
    assert "2 comments on v1, 1 open" in page
    assert "Too busy" not in page
    # The settings are fields in the section, not a dialog.
    assert 'name="expires_on"' in page and "Revoke Link" in page
    assert "Remove Password" not in page
    # A viewer reads it all but can't change it.
    viewer = role_client(Role.VIEWER)
    seen = viewer.get(
        panel_url(cue), {"version": str(version.pk)}
    ).content.decode()
    assert link.token in seen and "2 comments on v1" in seen
    assert 'name="expires_on"' not in seen and "Revoke Link" not in seen
    assert viewer.get(panel_url(cue), {"version": "999999"}).status_code == 404


def test_the_panel_says_what_the_link_is_set_to(
    role_client: Callable[[Role], Client],
    cue: models.Cue,
    link: models.ReviewLink,
) -> None:
    editor = role_client(Role.EDITOR)
    Client().get(page_url(link))
    link.refresh_from_db()
    review.set_password(link, "strings42")
    link.expires_on = datetime.date(2026, 11, 30)
    link.comments_open = False
    link.save()
    seen = editor.get(panel_url(cue)).content.decode()
    assert "Password set" in seen and "Works through Nov 30" in seen
    assert "Comments closed" in seen and "Last opened" in seen
    assert "No comments on v1 yet" in seen and "Remove Password" in seen
    assert "strings42" not in seen
    link.expires_on = datetime.date(2026, 11, 1)
    link.save()
    assert "Ended Nov 1" in editor.get(panel_url(cue)).content.decode()
    # A File Link that can't be played says so; a path isn't linked.
    cue.versions.update(file_link="/Volumes/Bounces/1m01.mov")
    seen = editor.get(panel_url(cue)).content.decode()
    assert "Not a Dropbox link to a single file" in seen
    assert "Open the Bounce" not in seen
    cue.versions.update(file_link="")
    seen = editor.get(panel_url(cue)).content.decode()
    assert "Paste a Dropbox link to v1" in seen


def test_a_cue_without_versions_or_a_link(
    role_client: Callable[[Role], Client],
) -> None:
    cue = factories.CueFactory.create()
    editor = role_client(Role.EDITOR).get(panel_url(cue)).content.decode()
    assert "Make a Review Link" in editor and "File Link" not in editor
    viewer = role_client(Role.VIEWER)
    assert "No review link." in viewer.get(panel_url(cue)).content.decode()
    review.create(cue, None)
    assert "comment" not in viewer.get(panel_url(cue)).content.decode()


def test_an_editor_makes_sets_up_and_revokes_a_link(
    role_client: Callable[[Role], Client], cue: models.Cue
) -> None:
    editor = role_client(Role.EDITOR)
    version = str(cue.versions.get().pk)
    made = editor.post(
        reverse("cues:review_create", args=[cue.pk]),
        {"version": version},
        headers=HX,
    )
    link = models.ReviewLink.objects.get(cue=cue)
    assert link.created_by == User.objects.get(role=Role.EDITOR)
    assert link.token in made.content.decode()
    settings_url = reverse("cues:review_link", args=[cue.pk])
    assert editor.get(settings_url).status_code == 405

    # Each setting posts itself when it changes, and only it is saved.
    def save(field: str, value: str = "") -> str:
        data = {"version": version, "field": field}
        if value:
            data[field] = value
        answer = editor.post(settings_url, data, headers=HX)
        assert answer.status_code == 200
        return str(answer.content.decode())

    shown = save("password", "strings42")
    # Only the status comes back, and the password field emptied again.
    assert 'id="review-status"' in shown and 'id="cue-review"' not in shown
    assert 'id="review-password-field" hx-swap-oob="true"' in shown
    assert "Password set" in shown and "strings42" not in shown
    assert "Works through Nov 30" in save("expires_on", "2026-11-30")
    shown = save("preroll", "0:02")
    assert "Pre-roll" in shown and "0:02" in shown
    assert "hx-swap-oob" not in shown
    link.refresh_from_db()
    assert review.check_password(link, "strings42")
    assert (link.expires_on, link.preroll, link.comments_open) == (
        datetime.date(2026, 11, 30),
        48,
        True,
    )
    again = editor.get(panel_url(cue)).content.decode()
    assert 'value="0:02"' in again and "strings42" not in again
    # A setting saved from a tab that shows old values touches only
    # itself: the last day and the password stay.
    assert "Comments closed" in save("comments_open")
    save("preroll", "00:00:01:12")
    save("password")
    link.refresh_from_db()
    assert review.check_password(link, "strings42")
    assert (link.expires_on, link.preroll, link.comments_open) == (
        datetime.date(2026, 11, 30),
        36,
        False,
    )
    assert 'value="00:00:01:12"' in editor.get(panel_url(cue)).content.decode()


def test_settings_on_an_ended_link_and_revoking(
    role_client: Callable[[Role], Client],
    cue: models.Cue,
    link: models.ReviewLink,
) -> None:
    editor = role_client(Role.EDITOR)
    version = str(cue.versions.get().pk)
    settings_url = reverse("cues:review_link", args=[cue.pk])
    review.set_password(link, "strings42")
    link.preroll = 36
    link.comments_open = False

    def save(field: str, value: str = "") -> str:
        data = {"version": version, "field": field}
        if value:
            data[field] = value
        answer = editor.post(settings_url, data, headers=HX)
        assert answer.status_code == 200
        return str(answer.content.decode())

    # Once the last day has passed, the other settings still save.
    link.expires_on = datetime.date(2026, 11, 1)
    link.save()
    assert "Ended Nov 1" in save("comments_open", "on")
    assert "Works through" not in save("expires_on")
    link.refresh_from_db()
    assert link.expires_on is None and link.comments_open
    assert editor.post(settings_url, {"field": "token"}).status_code == 400
    assert editor.post(settings_url, {}).status_code == 400
    removed = editor.post(settings_url, {"remove_password": "1"}, headers=HX)
    assert "No password" in removed.content.decode()
    link.refresh_from_db()
    assert (link.password, link.preroll) == ("", 36)
    # Revoking ends the link; the comments stay on the cue.
    review.add_comment(
        cue.versions.get(), name="Dana", body="Too busy", frame=1, user=None
    )
    revoked = editor.post(settings_url, {"revoke": "1"}, headers=HX)
    assert "Make a Review Link" in revoked.content.decode()
    assert not models.ReviewLink.objects.exists()
    assert models.ReviewComment.objects.count() == 1
    gone = editor.post(settings_url, {"field": "preroll"}, headers=HX)
    assert gone.status_code == 404


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("password", "abc", "Password: Use at least 6 characters."),
        ("expires_on", "2026-11-12", "Last Day: Choose today or a later"),
        ("preroll", "soon", "Pre-Roll: Enter a length like 0:02"),
    ],
)
def test_a_bad_setting_is_refused_alone(
    role_client: Callable[[Role], Client],
    link: models.ReviewLink,
    field: str,
    value: str,
    error: str,
) -> None:
    url = reverse("cues:review_link", args=[link.cue.pk])
    editor = role_client(Role.EDITOR)
    response = editor.post(url, {"field": field, field: value}, headers=HX)
    assert response.status_code == 200
    # The status comes back saying what's wrong and that it wasn't saved.
    shown = response.content.decode()
    assert error in shown and "Not saved." in shown
    assert 'id="review-status"' in shown and "hx-swap-oob" not in shown
    link.refresh_from_db()
    assert (link.password, link.expires_on, link.preroll) == ("", None, 0)
    # Another setting saves all the same, whatever that field still says.
    editor.post(url, {"field": "comments_open", field: value}, headers=HX)
    link.refresh_from_db()
    assert not link.comments_open


def test_a_pre_roll_reads_back_as_typed() -> None:
    drop = timecode.rate_for("29.97", drop_frame=True)
    whole = timecode.parse_duration("1:00", drop)
    assert review_views.preroll_text(whole, drop) == "1:00"
    assert review_views.preroll_text(whole + 1, drop) == "00:00:59;29"
    film = timecode.RATE_24
    assert review_views.preroll_text(48, film) == "0:02"
    assert review_views.preroll_text(36, film) == "00:00:01:12"
    assert review_views.preroll_text(0, film) == ""


def test_viewers_cannot_change_a_link(
    role_client: Callable[[Role], Client], link: models.ReviewLink
) -> None:
    viewer = role_client(Role.VIEWER)
    url = reverse("cues:review_link", args=[link.cue.pk])
    assert viewer.post(url, {"revoke": "1"}).status_code == 403
    create = reverse("cues:review_create", args=[link.cue.pk])
    assert viewer.post(create).status_code == 403
    assert models.ReviewLink.objects.exists()


# The audio alone.

AUDIO_LINK = "https://www.dropbox.com/scl/fi/def/1m01_v1_2mx.wav?rlkey=j&dl=0"


def test_a_versions_audio_plays_under_its_video(
    role_client: Callable[[Role], Client],
    cue: models.Cue,
    link: models.ReviewLink,
) -> None:
    page = Client().get(page_url(link)).content.decode()
    assert "review-audio" not in page
    version = cue.versions.get()
    editor = role_client(Role.EDITOR)
    saved = editor.post(
        reverse("cues:version_field", args=[version.pk, "audio_link"]),
        {
            "version-audio_link": f" {AUDIO_LINK} ",
            "updated_at": cue.updated_at.isoformat(),
        },
        headers=HX,
    )
    assert saved.status_code == 200
    version.refresh_from_db()
    assert version.audio_link == AUDIO_LINK
    page = Client().get(page_url(link)).content.decode()
    assert (
        '<audio id="review-audio-source" src="https://www.dropbox.com/scl/fi/'
        'def/1m01_v1_2mx.wav?rlkey=j&amp;raw=1"'
    ) in page
    (shown,) = review.playable(cue)
    assert shown.audio.endswith("raw=1")
    assert "The audio plays under the video." in (
        editor.get(panel_url(cue)).content.decode()
    )
    # Not a Dropbox file: no player, and the cue page says so.
    version.audio_link = "/Volumes/Bounces/1m01_2mx.wav"
    version.save()
    assert "review-audio" not in Client().get(page_url(link)).content.decode()
    assert "Audio Link isn" in editor.get(panel_url(cue)).content.decode()
    # Audio without video isn't a review page of its own.
    version.audio_link = AUDIO_LINK
    version.file_link = ""
    version.save()
    assert "The audio plays once File Link has a video." in (
        editor.get(panel_url(cue)).content.decode()
    )
    assert b"Nothing to Watch Yet" in Client().get(page_url(link)).content
