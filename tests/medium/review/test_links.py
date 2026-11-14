"""Review links: what a guest may do with one."""

from collections.abc import Callable
import datetime

from django.test import Client
from django.test import override_settings
from django.urls import reverse
import freezegun
import pytest

from accounts.models import Role
from accounts.models import User
from cues import models
from cues.services import activity
from cues.services import review
from cues.views import review as review_views
from tests import factories
from tests.medium.review.setup import FILE_LINK
from tests.medium.review.setup import comments_url
from tests.medium.review.setup import page_url
from tests.medium.review.setup import post

pytestmark = pytest.mark.django_db


# The link.


def test_a_cue_has_one_link_with_a_long_token(cue: models.Cue) -> None:
    user = factories.UserFactory.create()
    link = review.create(cue, user)
    assert len(link.token) >= 43
    assert link.created_by == user
    assert review.create(cue, None) == link
    assert str(link) == "Review link for 1m01 Main Title"


def test_a_guest_opens_the_page(link: models.ReviewLink) -> None:
    response = Client().get(page_url(link))
    assert response.status_code == 200
    page = response.content.decode()
    assert "Main Title" in page and "Night Ferry" in page
    # The file itself, not Dropbox's page about it.
    assert (
        'src="https://www.dropbox.com/scl/fi/abc/1m01_v1.mp4?rlkey=k&amp;raw=1"'
    ) in page
    assert '"firstFrame": 86640' in page and '"fps": [24, 1]' in page
    # Nothing of the app around it.
    assert "topbar" not in page and "/projects/" not in page
    link.refresh_from_db()
    assert link.last_opened_at is not None


def test_the_page_is_private_and_may_play_dropbox_video(
    link: models.ReviewLink,
) -> None:
    response = Client().get(page_url(link))
    assert response["Cache-Control"] == "no-store"
    assert response["X-Robots-Tag"] == "noindex, nofollow"
    policy = dict(
        part.strip().partition(" ")[::2]
        for part in response["Content-Security-Policy"].split(";")
    )
    assert policy["media-src"] == (
        "'self' https://www.dropbox.com https://*.dropboxusercontent.com"
    )
    assert policy["script-src"] == "'self'"
    # Only the review page: no other page may load media from elsewhere.
    other = Client().get("/auth/login/")["Content-Security-Policy"]
    assert "media-src" not in other and "dropbox" not in other


def test_unknown_revoked_and_expired_links_are_gone(
    link: models.ReviewLink,
) -> None:
    client = Client()
    response = client.get("/review/nonsense/")
    assert response.status_code == 404
    assert b"This Link Doesn" in response.content
    assert response["Cache-Control"] == "no-store"
    # Its last day still works; the day after doesn't.
    link.expires_on = datetime.date(2026, 11, 13)
    link.save()
    assert client.get(page_url(link)).status_code == 200
    link.expires_on = datetime.date(2026, 11, 12)
    link.save()
    assert review.expired(link)
    assert client.get(page_url(link)).status_code == 404
    assert client.get(comments_url(link), {"v": "v1"}).status_code == 404
    assert post(client, link)[0] == 404
    unlock = reverse("cues:review_unlock", args=[link.token])
    assert client.post(unlock, {"password": "x"}).status_code == 404
    link.expires_on = None
    link.save()
    token = link.token
    review.revoke(link)
    assert client.get(f"/review/{token}/").status_code == 404


def test_versions_without_video_are_left_out(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    # v2 is current but its File Link is a path: v1 is shown.
    v2 = factories.CueVersionFactory.create(
        cue=cue, label="v2", file_link="/Volumes/Bounces/1m01_v2.mov"
    )
    cue.current_version = v2
    cue.save()
    client = Client()
    page = client.get(page_url(link)).content.decode()
    assert "1m01_v1.mp4" in page and "review-versions" not in page
    assert client.get(page_url(link), {"v": "v2"}).status_code == 404
    # Once it has video, both are offered and the current one shows.
    v2.file_link = "https://www.dropbox.com/scl/fi/def/1m01_v2.mp4?rlkey=j"
    v2.save()
    page = client.get(page_url(link)).content.decode()
    assert "1m01_v2.mp4" in page and "1m01_v1.mp4" not in page
    assert 'href="?v=v1"' in page and 'aria-current="page"' in page
    assert (
        "1m01_v1.mp4"
        in client.get(page_url(link), {"v": "v1"}).content.decode()
    )


def test_a_cue_with_no_video_says_so(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    cue.versions.update(file_link="")
    response = Client().get(page_url(link))
    assert response.status_code == 200
    assert b"Nothing to Watch Yet" in response.content
    assert b"review.js" not in response.content
    assert Client().get(comments_url(link), {"v": "v1"}).status_code == 404


# Passwords.


def test_a_password_is_asked_once_per_session(
    link: models.ReviewLink,
) -> None:
    review.set_password(link, "strings42")
    link.save()
    assert "strings42" not in link.password
    client = Client()
    locked = client.get(page_url(link))
    assert locked.status_code == 200
    assert b"Enter the Password" in locked.content
    assert (
        b"dropbox" not in locked.content and b"Main Title" not in locked.content
    )
    # Locked, the comments aren't there either.
    assert client.get(comments_url(link), {"v": "v1"}).status_code == 404
    assert post(client, link)[0] == 404
    assert not models.ReviewComment.objects.exists()
    unlock = reverse("cues:review_unlock", args=[link.token])
    wrong = client.post(unlock, {"password": "strings43"})
    assert wrong.status_code == 200
    assert b"the password for this link" in wrong.content
    assert b"Enter the Password" in client.get(page_url(link)).content
    right = client.post(unlock, {"password": "strings42"})
    assert right.status_code == 302 and right["Location"] == page_url(link)
    assert b"Main Title" in client.get(page_url(link)).content
    assert post(client, link)[0] == 200
    # Another browser still has to give it.
    assert b"Enter the Password" in Client().get(page_url(link)).content


def test_changing_the_password_locks_the_link_again(
    link: models.ReviewLink,
) -> None:
    review.set_password(link, "strings42")
    link.save()
    client = Client()
    unlock = reverse("cues:review_unlock", args=[link.token])
    client.post(unlock, {"password": "strings42"})
    assert b"Main Title" in client.get(page_url(link)).content
    review.set_password(link, "woodwinds7")
    link.save()
    assert b"Enter the Password" in client.get(page_url(link)).content
    # Removed, the link is open; unlocking it needs nothing.
    review.set_password(link, "")
    link.save()
    assert b"Main Title" in Client().get(page_url(link)).content
    assert client.post(unlock, {}).status_code == 302
    assert not review.check_password(link, "")
    assert not review.unlocked(link, None)


def test_a_short_password_is_refused(link: models.ReviewLink) -> None:
    with pytest.raises(review.ReviewError, match="at least 6"):
        review.set_password(link, "abc")


def test_a_signed_in_member_skips_the_password(
    role_client: Callable[[Role], Client], link: models.ReviewLink
) -> None:
    review.set_password(link, "strings42")
    link.save()
    assert b"Main Title" in role_client(Role.VIEWER).get(page_url(link)).content
    # Signed in but without a role: a guest like any other.
    outsider = role_client(Role.NONE).get(page_url(link))
    assert b"Enter the Password" in outsider.content


# Comments.


def test_a_guest_comments_at_a_frame_and_overall(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    client = Client()
    status, page = post(client, link, frame="36", name="  Dana   Director ")
    assert status == 200
    comment = models.ReviewComment.objects.get()
    assert (comment.frame, comment.name, comment.body, comment.member) == (
        36,
        "Dana Director",
        "Too busy",
        False,
    )
    assert str(comment) == "Dana Director on 1m01 v1"
    # 01:00:10:00 plus 36 frames at 24 fps.
    assert "1:00:11:12" in page and 'data-frame="36"' in page
    # The name is kept for the session, and the form says so.
    assert "Commenting as" in page and 'value="Dana Director"' in page
    # Sent again with each comment, from the form's hidden field.
    post(
        client,
        link,
        frame="",
        name="Dana Director",
        body="Love the theme\nAll of it",
    )
    listed = review.comments(link, comment.version)
    assert [c.timecode for c in listed] == ["", "1:00:11:12"]
    again = client.get(comments_url(link), {"v": "v1"}).content.decode()
    assert "Overall" in again and "Love the theme<br>All of it" in again
    assert "hx-swap-oob" not in again  # only a post carries the form's bits
    assert client.get(page_url(link)).context["guest_name"] == "Dana Director"
    # Each is one change in the project's Activity, naming the guest.
    entries = activity.entries(
        list(cue.project.change_sets.select_related("user")[:2])
    )
    assert [(e.label, e.user, e.cues) for e in entries] == [
        ("Comment on 1m01 v1 by Dana Director (guest)", "", 1)
    ] * 2


def test_the_pre_roll_moves_the_timecodes(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    version = cue.versions.get()
    review.add_comment(version, name="Dana", body="Here", frame=48, user=None)
    link.preroll = 48
    link.save()
    assert review.first_frame(link, version) == 86_640 - 48
    assert review.comments(link, version)[0].timecode == "1:00:10:00"
    # Without the link (revoked), the version's IN alone.
    assert review.comments(None, version)[0].timecode == "1:00:12:00"
    # No IN anywhere: from zero, and never before it.
    version.tc_in = None
    version.save()
    cue.tc_in = cue.tc_out = None
    cue.save()
    version.refresh_from_db()
    assert review.first_frame(link, version) == 0


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({"body": "  "}, "Enter a comment."),
        ({"body": "x" * 2001}, "Keep a comment to 2,000 characters."),
        ({"name": ""}, "Enter your name."),
        ({"name": "n" * 61}, "Keep your name to 60 characters."),
        ({"frame": "-1"}, "That time isn"),
        ({"frame": "1.5"}, "That time isn"),
        ({"frame": "9999999999"}, "That time isn"),
        ({"frame": "999999999"}, "That time isn"),  # past 99:59:59:23
        ({"frame": "٣"}, "That time isn"),
    ],
)
def test_bad_comments_are_refused(
    link: models.ReviewLink, data: dict[str, str], error: str
) -> None:
    status, page = post(Client(), link, **data)
    assert status == 200
    assert error in page and 'id="review-form-error"' in page
    assert not models.ReviewComment.objects.exists()


def test_comments_are_escaped(link: models.ReviewLink) -> None:
    _, page = post(
        Client(),
        link,
        name="<b>Dana</b>",
        body="<script>alert(1)</script>\x00",
        frame="1",
    )
    assert "<script>alert" not in page and "<b>Dana" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "\x00" not in models.ReviewComment.objects.get().body


def test_comments_can_be_closed(link: models.ReviewLink) -> None:
    link.comments_open = False
    link.save()
    client = Client()
    page = client.get(page_url(link)).content.decode()
    assert "Comments are closed." in page and "review-form" not in page
    status, answer = post(client, link)
    assert status == 200 and review_views.CLOSED in answer
    assert not models.ReviewComment.objects.exists()


def test_a_version_takes_only_so_many_comments(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    version = cue.versions.get()
    with override_settings():
        review.MAX_COMMENTS, kept = 2, review.MAX_COMMENTS
        try:
            assert post(Client(), link)[0] == 200
            assert post(Client(), link)[0] == 200
            _, page = post(Client(), link)
        finally:
            review.MAX_COMMENTS = kept
    assert "1m01 v1 has 2 comments" in page
    assert version.review_comments.count() == 2


def test_a_comment_names_a_version_of_this_cue(
    link: models.ReviewLink,
) -> None:
    other = factories.CueVersionFactory.create(label="v9", file_link=FILE_LINK)
    assert other.cue != link.cue
    assert post(Client(), link, v="v9")[0] == 404
    assert post(Client(), link, v="")[0] == 404
    assert not models.ReviewComment.objects.exists()


def test_a_member_comments_under_their_own_name(
    role_client: Callable[[Role], Client], link: models.ReviewLink
) -> None:
    client = role_client(Role.VIEWER)
    user = User.objects.get(role=Role.VIEWER)
    status, page = post(client, link, name="Someone Else")
    assert status == 200
    comment = models.ReviewComment.objects.get()
    assert comment.member
    assert comment.name == activity.user_name(user) != "Someone Else"
    assert "Commenting as" in page
    assert review_views.NAME not in client.session


def test_guest_writes_need_the_csrf_token(link: models.ReviewLink) -> None:
    client = Client(enforce_csrf_checks=True)
    values = {"v": "v1", "name": "Dana", "body": "Too busy"}
    assert client.post(comments_url(link), values).status_code == 403
    unlock = reverse("cues:review_unlock", args=[link.token])
    assert client.post(unlock, {"password": "x"}).status_code == 403
    assert not models.ReviewComment.objects.exists()


# Rate limits.


@override_settings(RATELIMIT_REVIEW="3/m")
def test_the_page_is_limited_per_address(link: models.ReviewLink) -> None:
    client = Client()
    for _ in range(3):
        assert client.get(page_url(link)).status_code == 200
    assert client.get(page_url(link)).status_code == 429
    assert client.get(comments_url(link), {"v": "v1"}).status_code == 429
    assert (
        Client().get(page_url(link), REMOTE_ADDR="10.0.0.2").status_code == 200
    )


@override_settings(RATELIMIT_REVIEW_COMMENTS="2/m")
def test_comments_are_limited_per_address(link: models.ReviewLink) -> None:
    client = Client()
    assert post(client, link)[0] == 200
    assert post(client, link)[0] == 200
    assert post(client, link)[0] == 429
    assert models.ReviewComment.objects.count() == 2
    # Reading them isn't counted against posting.
    assert client.get(comments_url(link), {"v": "v1"}).status_code == 200


@override_settings(RATELIMIT_REVIEW_UNLOCK="2/m")
def test_password_tries_are_limited_per_address(
    link: models.ReviewLink,
) -> None:
    review.set_password(link, "strings42")
    link.save()
    unlock = reverse("cues:review_unlock", args=[link.token])
    client = Client()
    for _ in range(2):
        assert client.post(unlock, {"password": "nope"}).status_code == 200
    assert client.post(unlock, {"password": "strings42"}).status_code == 429


@override_settings(RATELIMIT_REVIEW_UNLOCK_LINK="2/h")
def test_wrong_passwords_are_limited_per_link(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    review.set_password(link, "strings42")
    link.save()
    unlock = reverse("cues:review_unlock", args=[link.token])
    with freezegun.freeze_time("2026-11-13 18:00:30") as clock:
        # Opening it with the right password doesn't count.
        for n in range(3):
            opened = Client().post(
                unlock, {"password": "strings42"}, REMOTE_ADDR=f"10.0.1.{n}"
            )
            assert opened.status_code == 302
        # Two wrong tries from different addresses use the link's hour up.
        for n in range(2):
            tried = Client().post(
                unlock, {"password": "nope"}, REMOTE_ADDR=f"10.0.0.{n}"
            )
            assert tried.status_code == 200
        blocked = Client().post(
            unlock, {"password": "strings42"}, REMOTE_ADDR="10.0.0.9"
        )
        assert blocked.status_code == 429
        # Still blocked after ten idle minutes: the count lasts the hour.
        clock.tick(datetime.timedelta(minutes=10))
        again = Client().post(
            unlock, {"password": "strings42"}, REMOTE_ADDR="10.0.0.9"
        )
        assert again.status_code == 429
        # Another cue's link is counted on its own.
        other = review.create(
            factories.CueFactory.create(project=cue.project, number="1m02"),
            None,
        )
        review.set_password(other, "woodwinds7")
        other.save()
        elsewhere = Client().post(
            reverse("cues:review_unlock", args=[other.token]),
            {"password": "woodwinds7"},
            REMOTE_ADDR="10.0.0.9",
        )
        assert elsewhere.status_code == 302


@override_settings(RATELIMIT_REVIEW_COMMENTS_LINK="2/h")
def test_comments_are_limited_per_link(link: models.ReviewLink) -> None:
    for n in range(2):
        posted = Client().post(
            comments_url(link),
            {"v": "v1", "name": "Dana", "body": "Too busy"},
            REMOTE_ADDR=f"10.0.0.{n}",
        )
        assert posted.status_code == 200
    flood = Client().post(
        comments_url(link),
        {"v": "v1", "name": "Dana", "body": "Too busy"},
        REMOTE_ADDR="10.0.0.9",
    )
    assert flood.status_code == 429
    assert models.ReviewComment.objects.count() == 2


def test_unlocking_gives_the_browser_a_new_session(
    link: models.ReviewLink,
) -> None:
    review.set_password(link, "strings42")
    link.save()
    client = Client()
    # A guest who commented elsewhere already has a session.
    session = client.session
    session["review_name"] = "Dana"
    session.save()
    before = client.session.session_key
    unlock = reverse("cues:review_unlock", args=[link.token])
    client.post(unlock, {"password": "strings42"})
    assert client.session.session_key != before
    assert client.session["review_name"] == "Dana"
