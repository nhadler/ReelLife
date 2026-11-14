"""Who watched a review page: the name asked first, visits, watch time."""

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
from cues.services import review_views
from tests.medium.review.setup import HX
from tests.medium.review.setup import comments_url
from tests.medium.review.setup import guest
from tests.medium.review.setup import page_url
from tests.medium.review.setup import panel_url

pytestmark = pytest.mark.django_db


def _who(link: models.ReviewLink) -> str:
    return reverse("cues:review_who", args=[link.token])


def _watching(link: models.ReviewLink) -> str:
    return reverse("cues:review_watching", args=[link.token])


def _visit_key(client: Client, link: models.ReviewLink) -> str:
    page = client.get(page_url(link))
    return str(page.context["player"]["visit"])


def test_a_guest_says_who_is_watching_first(link: models.ReviewLink) -> None:
    client = Client()
    asked = client.get(page_url(link))
    assert asked.status_code == 200
    assert b"Who's Watching?" in asked.content
    # Nothing of the cue yet, and no comments either.
    assert b"dropbox" not in asked.content
    assert b"Main Title" not in asked.content
    assert client.get(comments_url(link), {"v": "v1"}).status_code == 404
    posted = client.post(
        comments_url(link), {"v": "v1", "name": "Dana", "body": "Hi"}
    )
    assert posted.status_code == 404
    assert not models.ReviewView.objects.exists()
    # A name that doesn't show is asked for again.
    blank = client.post(_who(link), {"name": " \u200b "})
    assert blank.status_code == 200 and b"Enter your name." in blank.content
    named = client.post(_who(link), {"name": "  Dana   Director "})
    assert named.status_code == 302 and named["Location"] == page_url(link)
    page = client.get(page_url(link)).content.decode()
    assert "Main Title" in page and "Commenting as" in page
    assert "Dana Director" in page
    assert models.ReviewView.objects.get().name == "Dana Director"


def test_the_password_comes_before_the_name(link: models.ReviewLink) -> None:
    review.set_password(link, "strings42")
    link.save()
    client = Client()
    assert b"Enter the Password" in client.get(page_url(link)).content
    assert client.post(_who(link), {"name": "Dana"}).status_code == 404
    unlock = reverse("cues:review_unlock", args=[link.token])
    client.post(unlock, {"password": "strings42"})
    assert b"Who's Watching?" in client.get(page_url(link)).content
    client.post(_who(link), {"name": "Dana"})
    assert b"Main Title" in client.get(page_url(link)).content


def test_a_visit_is_recorded_and_a_reload_carries_it_on(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    client = guest("Dana")
    with freezegun.freeze_time("2026-11-13 18:00:00") as clock:
        key = _visit_key(client, link)
        visit = models.ReviewView.objects.get()
        assert (visit.name, visit.member, visit.played, visit.furthest) == (
            "Dana",
            False,
            0,
            None,
        )
        assert visit.key == key and visit.version == cue.versions.get()
        assert str(visit) == "Dana watched 1m01 v1"
        # A reload ten minutes on is the same visit, even renamed.
        clock.tick(datetime.timedelta(minutes=10))
        session = client.session
        session["review_name"] = "Dana D."
        session.save()
        assert _visit_key(client, link) == key
        visit.refresh_from_db()
        assert visit.name == "Dana D."
        # An hour of nothing, and it's a new one. Another browser too.
        clock.tick(datetime.timedelta(minutes=61))
        assert _visit_key(client, link) != key
        _visit_key(guest("Pat"), link)
    assert models.ReviewView.objects.count() == 3
    assert [v.name for v in models.ReviewView.objects.all()] == [
        "Pat",
        "Dana D.",
        "Dana D.",
    ]


def test_the_page_reports_what_was_watched(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    client = guest("Dana")
    with freezegun.freeze_time("2026-11-13 18:00:00") as clock:
        key = _visit_key(client, link)
        clock.tick(datetime.timedelta(seconds=90))
        sent = client.post(
            _watching(link), {"visit": key, "played": "75", "furthest": "1800"}
        )
        assert sent.status_code == 204
        assert sent["Cache-Control"] == "no-store"
        visit = models.ReviewView.objects.get()
        assert (visit.played, visit.furthest) == (75, 1800)
        # Neither goes back down, and a frame can be left out.
        client.post(_watching(link), {"visit": key, "played": "10"})
        client.post(
            _watching(link), {"visit": key, "played": "x", "furthest": "-5"}
        )
        visit.refresh_from_db()
        assert (visit.played, visit.furthest) == (75, 1800)
        # Play time can't pass the time since the visit began (and a bit).
        clock.tick(datetime.timedelta(seconds=30))
        client.post(
            _watching(link),
            {"visit": key, "played": "99999", "furthest": "999999999"},
        )
        visit.refresh_from_db()
        assert visit.played == 120 + 30
        assert visit.furthest == 1800  # past 99:59:59:23 is ignored
    (listed,) = review_views.visits(link, cue.versions.get())
    assert (listed.played, listed.open_for, listed.reached) == (
        "2:30",
        "2:00",
        "1:01:25:00",  # 01:00:10:00 plus 1,800 frames at 24 fps
    )


def test_reports_need_the_visits_key_and_an_open_link(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    client = guest("Dana")
    key = _visit_key(client, link)
    assert client.post(_watching(link), {"visit": "nope"}).status_code == 404
    assert client.post(_watching(link), {}).status_code == 404
    assert client.get(_watching(link)).status_code == 405
    # A visit to another cue's version isn't this link's to report on.
    other = review.create(
        review.playable(cue)[0].version.cue.project.cues.create(
            number="1m02", picture=cue.picture
        ),
        None,
    )
    assert client.post(_watching(other), {"visit": key}).status_code == 404
    # No CSRF token, no report.
    strict = Client(enforce_csrf_checks=True)
    assert strict.post(_watching(link), {"visit": key}).status_code == 403
    # Locked or revoked, neither.
    review.set_password(link, "strings42")
    link.save()
    assert client.post(_watching(link), {"visit": key}).status_code == 404
    review.revoke(link)
    assert client.post(_watching(link), {"visit": key}).status_code == 404


@override_settings(RATELIMIT_REVIEW_WATCHING="2/m")
def test_reports_are_limited(link: models.ReviewLink) -> None:
    client = guest("Dana")
    key = _visit_key(client, link)
    for _ in range(2):
        assert client.post(_watching(link), {"visit": key}).status_code == 204
    assert client.post(_watching(link), {"visit": key}).status_code == 429


def test_the_team_who_sent_it_arent_counted(
    role_client: Callable[[Role], Client],
    cue: models.Cue,
    link: models.ReviewLink,
) -> None:
    # An editor checking the page isn't a view.
    page = role_client(Role.EDITOR).get(page_url(link))
    assert "visit" not in page.context["player"]
    assert not models.ReviewView.objects.exists()
    # A viewer (a director with an account, say) is, under their own name.
    viewer = role_client(Role.VIEWER)
    assert "visit" in viewer.get(page_url(link)).context["player"]
    visit = models.ReviewView.objects.get()
    user = User.objects.get(role=Role.VIEWER)
    assert (visit.name, visit.member) == (activity.user_name(user), True)
    del cue


def test_a_version_keeps_only_its_newest_visits(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    del link
    version = cue.versions.get()
    kept = review_views.MAX_VIEWS
    review_views.MAX_VIEWS = 3
    try:
        with freezegun.freeze_time("2026-11-13 18:00:00") as clock:
            for n in range(5):
                clock.tick(datetime.timedelta(minutes=1))
                review_views.start(
                    version,
                    name=f"Guest {n}",
                    member=False,
                    browser=review_views.new_browser(),
                )
    finally:
        review_views.MAX_VIEWS = kept
    assert [v.name for v in version.review_views.all()] == [
        "Guest 4",
        "Guest 3",
        "Guest 2",
    ]


def test_the_cue_page_lists_who_watched(
    role_client: Callable[[Role], Client],
    cue: models.Cue,
    link: models.ReviewLink,
) -> None:
    editor = role_client(Role.EDITOR)
    assert "Views" not in editor.get(panel_url(cue)).content.decode()
    client = guest("Dana Director")
    key = _visit_key(client, link)
    client.post(
        _watching(link), {"visit": key, "played": "20", "furthest": "240"}
    )
    shown = editor.get(panel_url(cue), headers=HX).content.decode()
    assert "Views" in shown and "Dana Director" in shown
    assert "0:20" in shown and "1:00:20:00" in shown
    # Visits go with their version, and outlast a revoked link.
    review.revoke(link)
    assert "Dana Director" in editor.get(panel_url(cue)).content.decode()
    cue.versions.all().delete()
    assert not models.ReviewView.objects.exists()
