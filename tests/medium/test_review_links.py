"""Review links: what a guest may do with one, and the cue page's side."""

from collections.abc import Callable, Iterator
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
from cues.services import authoring
from cues.services import history
from cues.services import review
from cues.services import trash
from cues.views import review as review_views
from tests import factories

pytestmark = pytest.mark.django_db

FILE_LINK = "https://www.dropbox.com/scl/fi/abc/1m01_v1.mp4?rlkey=k&dl=0"
HX = {"HX-Request": "true"}


@pytest.fixture(autouse=True)
def fixture_today() -> Iterator[None]:
    with freezegun.freeze_time("2026-11-13 18:00:00"):
        yield


@pytest.fixture(name="cue")
def fixture_cue() -> models.Cue:
    """1m01 at 24 fps, IN 01:00:10:00, with v1's bounce on Dropbox."""
    film = factories.ProjectFactory.create(title="Night Ferry", frame_rate="24")
    cue = factories.CueFactory.create(
        project=film, number="1m01", title="Main Title"
    )
    version = factories.CueVersionFactory.create(
        cue=cue, label="v1", file_link=FILE_LINK
    )
    cue.current_version = version
    cue.save()
    return cue


@pytest.fixture(name="link")
def fixture_link(cue: models.Cue) -> models.ReviewLink:
    return review.create(cue, None)


def _page(link: models.ReviewLink) -> str:
    return reverse("cues:review", args=[link.token])


def _comments(link: models.ReviewLink) -> str:
    return reverse("cues:review_comments", args=[link.token])


def _post(
    client: Client, link: models.ReviewLink, **data: str
) -> tuple[int, str]:
    values = {"v": "v1", "name": "Dana", "body": "Too busy", **data}
    response = client.post(_comments(link), values, headers=HX)
    return response.status_code, response.content.decode()


# The link.


def test_a_cue_has_one_link_with_a_long_token(cue: models.Cue) -> None:
    user = factories.UserFactory.create()
    link = review.create(cue, user)
    assert len(link.token) >= 43
    assert link.created_by == user
    assert review.create(cue, None) == link
    assert str(link) == "Review link for 1m01 Main Title"


def test_a_guest_opens_the_page(link: models.ReviewLink) -> None:
    response = Client().get(_page(link))
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
    response = Client().get(_page(link))
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
    assert client.get(_page(link)).status_code == 200
    link.expires_on = datetime.date(2026, 11, 12)
    link.save()
    assert review.expired(link)
    assert client.get(_page(link)).status_code == 404
    assert client.get(_comments(link), {"v": "v1"}).status_code == 404
    assert _post(client, link)[0] == 404
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
    page = client.get(_page(link)).content.decode()
    assert "1m01_v1.mp4" in page and "review-versions" not in page
    assert client.get(_page(link), {"v": "v2"}).status_code == 404
    # Once it has video, both are offered and the current one shows.
    v2.file_link = "https://www.dropbox.com/scl/fi/def/1m01_v2.mp4?rlkey=j"
    v2.save()
    page = client.get(_page(link)).content.decode()
    assert "1m01_v2.mp4" in page and "1m01_v1.mp4" not in page
    assert 'href="?v=v1"' in page and 'aria-current="page"' in page
    assert (
        "1m01_v1.mp4" in client.get(_page(link), {"v": "v1"}).content.decode()
    )


def test_a_cue_with_no_video_says_so(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    cue.versions.update(file_link="")
    response = Client().get(_page(link))
    assert response.status_code == 200
    assert b"Nothing to Watch Yet" in response.content
    assert b"review.js" not in response.content
    assert Client().get(_comments(link), {"v": "v1"}).status_code == 404


# Passwords.


def test_a_password_is_asked_once_per_session(
    link: models.ReviewLink,
) -> None:
    review.set_password(link, "strings42")
    link.save()
    assert "strings42" not in link.password
    client = Client()
    locked = client.get(_page(link))
    assert locked.status_code == 200
    assert b"Enter the Password" in locked.content
    assert (
        b"dropbox" not in locked.content and b"Main Title" not in locked.content
    )
    # Locked, the comments aren't there either.
    assert client.get(_comments(link), {"v": "v1"}).status_code == 404
    assert _post(client, link)[0] == 404
    assert not models.ReviewComment.objects.exists()
    unlock = reverse("cues:review_unlock", args=[link.token])
    wrong = client.post(unlock, {"password": "strings43"})
    assert wrong.status_code == 200
    assert b"the password for this link" in wrong.content
    assert b"Enter the Password" in client.get(_page(link)).content
    right = client.post(unlock, {"password": "strings42"})
    assert right.status_code == 302 and right["Location"] == _page(link)
    assert b"Main Title" in client.get(_page(link)).content
    assert _post(client, link)[0] == 200
    # Another browser still has to give it.
    assert b"Enter the Password" in Client().get(_page(link)).content


def test_changing_the_password_locks_the_link_again(
    link: models.ReviewLink,
) -> None:
    review.set_password(link, "strings42")
    link.save()
    client = Client()
    unlock = reverse("cues:review_unlock", args=[link.token])
    client.post(unlock, {"password": "strings42"})
    assert b"Main Title" in client.get(_page(link)).content
    review.set_password(link, "woodwinds7")
    link.save()
    assert b"Enter the Password" in client.get(_page(link)).content
    # Removed, the link is open; unlocking it needs nothing.
    review.set_password(link, "")
    link.save()
    assert b"Main Title" in Client().get(_page(link)).content
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
    assert b"Main Title" in role_client(Role.VIEWER).get(_page(link)).content
    # Signed in but without a role: a guest like any other.
    outsider = role_client(Role.NONE).get(_page(link))
    assert b"Enter the Password" in outsider.content


# Comments.


def test_a_guest_comments_at_a_frame_and_overall(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    client = Client()
    status, page = _post(client, link, frame="36", name="  Dana   Director ")
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
    _post(
        client,
        link,
        frame="",
        name="Dana Director",
        body="Love the theme\nAll of it",
    )
    listed = review.comments(link, comment.version)
    assert [c.timecode for c in listed] == ["", "1:00:11:12"]
    again = client.get(_comments(link), {"v": "v1"}).content.decode()
    assert "Overall" in again and "Love the theme<br>All of it" in again
    assert "hx-swap-oob" not in again  # only a post carries the form's bits
    assert client.get(_page(link)).context["guest_name"] == "Dana Director"
    # Each is one change in the project's Activity, naming the guest.
    entries = activity.entries(
        list(cue.project.change_sets.select_related("user")[:2])
    )
    assert [(e.label, e.user, e.cues) for e in entries] == [
        ("Comment on 1m01 v1 by Dana Director", "", 1)
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
    status, page = _post(Client(), link, **data)
    assert status == 200
    assert error in page and 'id="review-form-error"' in page
    assert not models.ReviewComment.objects.exists()


def test_comments_are_escaped(link: models.ReviewLink) -> None:
    _, page = _post(
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
    page = client.get(_page(link)).content.decode()
    assert "Comments are closed." in page and "review-form" not in page
    status, answer = _post(client, link)
    assert status == 200 and review_views.CLOSED in answer
    assert not models.ReviewComment.objects.exists()


def test_a_version_takes_only_so_many_comments(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    version = cue.versions.get()
    with override_settings():
        review.MAX_COMMENTS, kept = 2, review.MAX_COMMENTS
        try:
            assert _post(Client(), link)[0] == 200
            assert _post(Client(), link)[0] == 200
            _, page = _post(Client(), link)
        finally:
            review.MAX_COMMENTS = kept
    assert "1m01 v1 has 2 comments" in page
    assert version.review_comments.count() == 2


def test_a_comment_names_a_version_of_this_cue(
    link: models.ReviewLink,
) -> None:
    other = factories.CueVersionFactory.create(label="v9", file_link=FILE_LINK)
    assert other.cue != link.cue
    assert _post(Client(), link, v="v9")[0] == 404
    assert _post(Client(), link, v="")[0] == 404
    assert not models.ReviewComment.objects.exists()


def test_a_member_comments_under_their_own_name(
    role_client: Callable[[Role], Client], link: models.ReviewLink
) -> None:
    client = role_client(Role.VIEWER)
    user = User.objects.get(role=Role.VIEWER)
    status, page = _post(client, link, name="Someone Else")
    assert status == 200
    comment = models.ReviewComment.objects.get()
    assert comment.member
    assert comment.name == activity.user_name(user) != "Someone Else"
    assert "Commenting as" in page
    assert review_views.NAME not in client.session


def test_guest_writes_need_the_csrf_token(link: models.ReviewLink) -> None:
    client = Client(enforce_csrf_checks=True)
    values = {"v": "v1", "name": "Dana", "body": "Too busy"}
    assert client.post(_comments(link), values).status_code == 403
    unlock = reverse("cues:review_unlock", args=[link.token])
    assert client.post(unlock, {"password": "x"}).status_code == 403
    assert not models.ReviewComment.objects.exists()


# Rate limits.


@override_settings(RATELIMIT_REVIEW="3/m")
def test_the_page_is_limited_per_address(link: models.ReviewLink) -> None:
    client = Client()
    for _ in range(3):
        assert client.get(_page(link)).status_code == 200
    assert client.get(_page(link)).status_code == 429
    assert client.get(_comments(link), {"v": "v1"}).status_code == 429
    assert Client().get(_page(link), REMOTE_ADDR="10.0.0.2").status_code == 200


@override_settings(RATELIMIT_REVIEW_COMMENTS="2/m")
def test_comments_are_limited_per_address(link: models.ReviewLink) -> None:
    client = Client()
    assert _post(client, link)[0] == 200
    assert _post(client, link)[0] == 200
    assert _post(client, link)[0] == 429
    assert models.ReviewComment.objects.count() == 2
    # Reading them isn't counted against posting.
    assert client.get(_comments(link), {"v": "v1"}).status_code == 200


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
def test_password_tries_are_limited_per_link(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    review.set_password(link, "strings42")
    link.save()
    unlock = reverse("cues:review_unlock", args=[link.token])
    # From different addresses, against the one link.
    for n in range(2):
        tried = Client().post(
            unlock, {"password": "nope"}, REMOTE_ADDR=f"10.0.0.{n}"
        )
        assert tried.status_code == 200
    blocked = Client().post(
        unlock, {"password": "strings42"}, REMOTE_ADDR="10.0.0.9"
    )
    assert blocked.status_code == 429
    # Another cue's link is counted on its own.
    other = review.create(
        factories.CueFactory.create(project=cue.project, number="1m02"), None
    )
    review.set_password(other, "woodwinds7")
    other.save()
    opened = Client().post(
        reverse("cues:review_unlock", args=[other.token]),
        {"password": "woodwinds7"},
        REMOTE_ADDR="10.0.0.9",
    )
    assert opened.status_code == 302


# Undo.


def test_a_comment_undoes_and_comes_back_with_its_cue(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    version = cue.versions.get()
    comment = review.add_comment(
        version, name="Dana", body="Too busy", frame=12, user=None
    )
    review.delete_comment(comment, None)
    deleted = models.ChangeSet.objects.first()
    assert deleted is not None
    assert deleted.label == "Delete comment on 1m01 v1 by Dana"
    history.undo(deleted)
    assert models.ReviewComment.objects.get().body == "Too busy"
    # Deleting the cue takes its comments and its link; undo brings the
    # comments back, but a link is revoked for good.
    token = link.token
    authoring.delete_cue(cue)
    assert not models.ReviewComment.objects.exists()
    assert Client().get(f"/review/{token}/").status_code == 404
    gone = models.ChangeSet.objects.first()
    assert gone is not None
    history.undo(gone)
    assert models.ReviewComment.objects.get().frame == 12
    assert not models.ReviewLink.objects.exists()


def test_the_trash_restores_a_cues_comments(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    del link
    review.add_comment(
        cue.versions.get(), name="Dana", body="Too busy", frame=12, user=None
    )
    project, pk = cue.project, cue.pk
    authoring.delete_cue(cue)
    assert not models.ReviewComment.objects.exists()
    back = trash.restore(project, pk)
    comment = models.ReviewComment.objects.get()
    assert (comment.version.cue, comment.body) == (back, "Too busy")


# The cue page's side.


def _panel(cue: models.Cue) -> str:
    return reverse("cues:cue_review", args=[cue.pk])


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
        _panel(cue), {"version": str(version.pk)}
    ).content.decode()
    assert link.token in seen and "2 comments on v1" in seen
    assert 'name="expires_on"' not in seen and "Revoke Link" not in seen
    assert viewer.get(_panel(cue), {"version": "999999"}).status_code == 404


def test_the_panel_says_what_the_link_is_set_to(
    role_client: Callable[[Role], Client],
    cue: models.Cue,
    link: models.ReviewLink,
) -> None:
    editor = role_client(Role.EDITOR)
    Client().get(_page(link))
    link.refresh_from_db()
    review.set_password(link, "strings42")
    link.expires_on = datetime.date(2026, 11, 30)
    link.comments_open = False
    link.save()
    seen = editor.get(_panel(cue)).content.decode()
    assert "Password set" in seen and "Works through Nov 30" in seen
    assert "Comments closed" in seen and "Last opened" in seen
    assert "No comments on v1 yet" in seen and "Remove Password" in seen
    assert "strings42" not in seen
    link.expires_on = datetime.date(2026, 11, 1)
    link.save()
    assert "Ended Nov 1" in editor.get(_panel(cue)).content.decode()
    # A File Link that can't be played says so; a path isn't linked.
    cue.versions.update(file_link="/Volumes/Bounces/1m01.mov")
    seen = editor.get(_panel(cue)).content.decode()
    assert "Not a Dropbox link to a single file" in seen
    assert "Open the Bounce" not in seen
    cue.versions.update(file_link="")
    seen = editor.get(_panel(cue)).content.decode()
    assert "Paste a Dropbox link to v1" in seen


def test_a_cue_without_versions_or_a_link(
    role_client: Callable[[Role], Client],
) -> None:
    cue = factories.CueFactory.create()
    editor = role_client(Role.EDITOR).get(_panel(cue)).content.decode()
    assert "Make a Review Link" in editor and "File Link" not in editor
    viewer = role_client(Role.VIEWER)
    assert "No review link." in viewer.get(_panel(cue)).content.decode()
    review.create(cue, None)
    assert "comment" not in viewer.get(_panel(cue)).content.decode()


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
    # The section posts its settings whenever one changes.
    saved = editor.post(
        settings_url,
        {
            "version": version,
            "password": "strings42",
            "expires_on": "2026-11-30",
            "preroll": "0:02",
            "comments_open": "on",
        },
        headers=HX,
    )
    assert saved.status_code == 200
    shown = saved.content.decode()
    assert "Password set" in shown and "Works through Nov 30" in shown
    # Only the status comes back, and the password field emptied again.
    assert 'id="review-status"' in shown and 'id="cue-review"' not in shown
    assert 'id="review-password-field" hx-swap-oob="true"' in shown
    assert "strings42" not in shown
    again = editor.get(_panel(cue)).content.decode()
    assert 'value="00:00:02:00"' in again and "strings42" not in again
    link.refresh_from_db()
    assert review.check_password(link, "strings42")
    assert (link.expires_on, link.preroll, link.comments_open) == (
        datetime.date(2026, 11, 30),
        48,
        True,
    )
    # Left empty, the password stays; a timecode pre-roll counts frames.
    editor.post(settings_url, {"preroll": "00:00:01:12"}, headers=HX)
    link.refresh_from_db()
    assert review.check_password(link, "strings42")
    assert (link.expires_on, link.preroll, link.comments_open) == (
        None,
        36,
        False,
    )
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
    assert editor.post(settings_url, {}, headers=HX).status_code == 404


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({"password": "abc"}, "Use at least 6 characters."),
        ({"expires_on": "2026-11-12"}, "Choose today or a later day."),
        ({"preroll": "soon"}, "Enter a length like 0:02"),
    ],
)
def test_bad_settings_are_refused(
    role_client: Callable[[Role], Client],
    link: models.ReviewLink,
    data: dict[str, str],
    error: str,
) -> None:
    url = reverse("cues:review_link", args=[link.cue.pk])
    response = role_client(Role.EDITOR).post(url, data, headers=HX)
    assert response.status_code == 200
    # The status comes back with the error.
    assert error in response.content.decode()
    assert 'id="review-status"' in response.content.decode()
    link.refresh_from_db()
    assert (link.password, link.expires_on, link.preroll) == ("", None, 0)


def test_viewers_cannot_change_a_link(
    role_client: Callable[[Role], Client], link: models.ReviewLink
) -> None:
    viewer = role_client(Role.VIEWER)
    url = reverse("cues:review_link", args=[link.cue.pk])
    assert viewer.post(url, {"revoke": "1"}).status_code == 403
    create = reverse("cues:review_create", args=[link.cue.pk])
    assert viewer.post(create).status_code == 403
    assert models.ReviewLink.objects.exists()


# Replies and resolving.


def test_a_guest_replies_to_a_note(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    version = cue.versions.get()
    note = review.add_comment(
        version, name="Dana", body="Too busy", frame=24, user=None
    )
    client = Client()
    status, page = _post(
        client, link, name="Pat", body="Agreed", parent=str(note.pk), frame="99"
    )
    assert status == 200
    reply = models.ReviewComment.objects.get(parent=note)
    # A reply is about its note, not a frame.
    assert (reply.name, reply.body, reply.frame) == ("Pat", "Agreed", None)
    assert page.count("review-reply") >= 1 and "Agreed" in page
    assert f'data-reply="{note.pk}"' in page
    listed = review.comments(link, version)
    assert [(c.comment, c.replies) for c in listed] == [(note, [reply])]
    assert models.ChangeSet.objects.latest("pk").label == (
        "Reply to Dana on 1m01 v1 by Pat"
    )
    # Not to a reply, another version's note, or one that's gone.
    other = review.add_comment(
        factories.CueVersionFactory.create(cue=cue, label="v2"),
        name="Dana",
        body="Elsewhere",
        frame=None,
        user=None,
    )
    for parent in (str(reply.pk), str(other.pk), "999999", "x"):
        _, answer = _post(client, link, parent=parent)
        assert "That comment isn" in answer
    assert models.ReviewComment.objects.count() == 3
    with pytest.raises(review.ReviewError, match="can't be replied to"):
        review.add_comment(
            version, name="Pat", body="No", frame=None, user=None, parent=reply
        )


def test_an_editor_resolves_and_reopens_on_the_review_page(
    role_client: Callable[[Role], Client],
    cue: models.Cue,
    link: models.ReviewLink,
) -> None:
    version = cue.versions.get()
    note = review.add_comment(
        version, name="Dana", body="Too busy", frame=24, user=None
    )
    reply = review.add_comment(
        version, name="Pat", body="Agreed", frame=None, user=None, parent=note
    )
    # Guests and viewers see no Resolve or Delete.
    viewer = role_client(Role.VIEWER)
    for client in (Client(), viewer):
        page = client.get(_page(link)).content.decode()
        assert "Reply" in page
        assert "Resolve" not in page and "Delete" not in page
    editor = role_client(Role.EDITOR)
    page = editor.get(_page(link)).content.decode()
    assert "Resolve the comment by Dana at 1:00:11:00" in page
    assert "Delete the reply by Pat" in page
    url = reverse("cues:review_comment_resolve", args=[note.pk])
    assert viewer.post(url).status_code == 403
    done = editor.post(url, headers=HX)
    assert done.status_code == 204 and "review:changed" in done["HX-Trigger"]
    note.refresh_from_db()
    user = User.objects.get(role=Role.EDITOR)
    assert note.resolved_at is not None
    assert note.resolved_by == activity.user_name(user)
    assert models.ChangeSet.objects.latest("pk").label == (
        "Resolve comment on 1m01 v1 by Dana"
    )
    page = Client().get(_page(link)).content.decode()
    assert "is-resolved" in page and "Resolved" in page
    assert review.note_counts(version) == (1, 0)
    # Again changes nothing; resolved=0 reopens; undo resolves again.
    count = models.ChangeSet.objects.count()
    editor.post(url, headers=HX)
    assert models.ChangeSet.objects.count() == count
    editor.post(url, {"resolved": "0"}, headers=HX)
    note.refresh_from_db()
    assert note.resolved_at is None and note.resolved_by == ""
    history.undo(models.ChangeSet.objects.latest("pk"))
    note.refresh_from_db()
    assert note.resolved_at is not None
    # A reply isn't resolved on its own.
    bad = editor.post(
        reverse("cues:review_comment_resolve", args=[reply.pk]), headers=HX
    )
    assert bad.status_code == 400


def test_an_editor_deletes_a_note_with_its_replies(
    role_client: Callable[[Role], Client],
    cue: models.Cue,
    link: models.ReviewLink,
) -> None:
    del link
    version = cue.versions.get()
    note = review.add_comment(
        version, name="Dana", body="Too busy", frame=1, user=None
    )
    reply = review.add_comment(
        version, name="Pat", body="Agreed", frame=None, user=None, parent=note
    )
    editor = role_client(Role.EDITOR)
    # A reply on its own.
    url = reverse("cues:review_comment_delete", args=[reply.pk])
    assert role_client(Role.VIEWER).post(url).status_code == 403
    response = editor.post(url, headers=HX)
    assert response.status_code == 204
    assert "review:changed" in response["HX-Trigger"]
    change = models.ChangeSet.objects.latest("pk")
    assert change.label == "Delete reply on 1m01 v1 by Pat"
    assert change.user == User.objects.get(role=Role.EDITOR)
    history.undo(change)
    # The note takes its replies with it, and undo brings both back.
    editor.post(
        reverse("cues:review_comment_delete", args=[note.pk]), headers=HX
    )
    assert not models.ReviewComment.objects.exists()
    history.undo(models.ChangeSet.objects.latest("pk"))
    assert models.ReviewComment.objects.get(parent=None).body == "Too busy"
    assert models.ReviewComment.objects.get(parent__isnull=False).body == (
        "Agreed"
    )


def test_replies_come_back_with_a_deleted_cue(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    del link
    version = cue.versions.get()
    note = review.add_comment(
        version, name="Dana", body="Too busy", frame=1, user=None
    )
    review.add_comment(
        version, name="Pat", body="Agreed", frame=None, user=None, parent=note
    )
    project, pk = cue.project, cue.pk
    authoring.delete_cue(cue)
    history.undo(models.ChangeSet.objects.latest("pk"))
    assert models.ReviewComment.objects.count() == 2
    authoring.delete_cue(models.Cue.objects.get(pk=pk))
    assert not models.ReviewComment.objects.exists()
    trash.restore(project, pk)
    assert models.ReviewComment.objects.get(parent__isnull=False).parent == (
        models.ReviewComment.objects.get(parent=None)
    )


def test_open_notes_count_the_current_versions_unresolved_notes(
    role_client: Callable[[Role], Client],
    cue: models.Cue,
) -> None:
    v1 = cue.versions.get()
    first = review.add_comment(v1, name="Dana", body="A", frame=1, user=None)
    second = review.add_comment(v1, name="Dana", body="B", frame=2, user=None)
    review.add_comment(
        v1, name="Pat", body="C", frame=None, user=None, parent=first
    )
    assert review.open_notes([cue.pk]) == {cue.pk: 2}
    review.resolve(second, None)
    assert review.open_notes([cue.pk]) == {cue.pk: 1}
    editor = role_client(Role.EDITOR)
    grid = editor.get(reverse("cues:api_grid", args=[cue.project.pk])).json()
    assert [row["open_notes"] for row in grid["rows"]] == [1]
    assert {"id": "open_notes", "label": "Open Notes"}.items() <= next(
        c for c in grid["columns"] if c["id"] == "open_notes"
    ).items()
    # A new current version starts with none.
    v2 = factories.CueVersionFactory.create(cue=cue, label="v2")
    cue.current_version = v2
    cue.save()
    assert not review.open_notes([cue.pk])
    grid = editor.get(reverse("cues:api_grid", args=[cue.project.pk])).json()
    assert grid["rows"][0]["open_notes"] is None
