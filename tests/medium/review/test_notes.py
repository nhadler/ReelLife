"""Review comments: replies, resolving, undo, and what a review found."""

from collections.abc import Callable

from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from accounts.models import User
from cues import models
from cues.services import activity
from cues.services import authoring
from cues.services import history
from cues.services import review
from cues.services import trash
from tests import factories
from tests.medium.review.setup import HX
from tests.medium.review.setup import comments_url
from tests.medium.review.setup import page_url
from tests.medium.review.setup import panel_url
from tests.medium.review.setup import post

pytestmark = pytest.mark.django_db


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
    assert models.ChangeSet.objects.filter(
        label="Comment on 1m01 v1 by Dana (guest)"
    ).exists()
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


# Replies and resolving.


def test_a_guest_replies_to_a_note(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    version = cue.versions.get()
    note = review.add_comment(
        version, name="Dana", body="Too busy", frame=24, user=None
    )
    client = Client()
    status, page = post(
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
        "Reply to Dana on 1m01 v1 by Pat (guest)"
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
        _, answer = post(client, link, parent=parent)
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
        page = client.get(page_url(link)).content.decode()
        assert "Reply" in page
        assert "Resolve" not in page and "Delete" not in page
    editor = role_client(Role.EDITOR)
    page = editor.get(page_url(link)).content.decode()
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
    page = Client().get(page_url(link)).content.decode()
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


# From the adversarial review.


def test_undo_does_not_take_others_comments_with_it(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    del link
    version = cue.versions.get()
    note = review.add_comment(
        version, name="Dana", body="Too busy", frame=1, user=None
    )
    made_note = models.ChangeSet.objects.latest("pk")
    review.add_comment(
        version, name="Pat", body="Agreed", frame=None, user=None, parent=note
    )
    # Undoing the note would drop Pat's reply too: refused.
    with pytest.raises(history.UndoError, match="replied to since"):
        history.undo(made_note)
    assert models.ReviewComment.objects.count() == 2
    # The same for a version made, then commented on.
    v2 = authoring.create_version(
        models.CueVersion(cue=cue, label="v2"), update_cue=False
    )
    made_version = models.ChangeSet.objects.latest("pk")
    review.add_comment(v2, name="Dana", body="Better", frame=None, user=None)
    with pytest.raises(history.UndoError, match="commented on 1m01 v2"):
        history.undo(made_version)
    assert models.ReviewComment.objects.count() == 3
    # Without comments since, both undo as before.
    models.ReviewComment.objects.filter(version=v2).delete()
    history.undo(made_version)
    assert not cue.versions.filter(label="v2").exists()
    reply = models.ReviewComment.objects.get(parent=note)
    review.delete_comment(reply, None)
    history.undo(made_note)
    assert not models.ReviewComment.objects.exists()


def test_activity_copes_with_a_version_that_has_no_history(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    del link
    version = cue.versions.get()
    review.add_comment(version, name="Dana", body="Hi", frame=None, user=None)
    models.CueVersion.history.filter(id=version.pk).delete()  # pylint: disable=no-member
    entries = activity.entries(
        list(cue.project.change_sets.select_related("user")[:1])
    )
    assert [entry.cues for entry in entries] == [0]


@pytest.mark.parametrize(
    "name",
    ["\u200b\u200b", "\u2060", "\ufeff\u00ad", "\u202e\u202c", "\x07\x1b"],
)
def test_a_name_must_show(link: models.ReviewLink, name: str) -> None:
    _, page = post(Client(), link, name=name)
    assert "Enter your name." in page
    assert not models.ReviewComment.objects.exists()


def test_hidden_characters_are_left_out_of_names(
    link: models.ReviewLink,
) -> None:
    # A right-to-left override would show the name backwards.
    post(Client(), link, name="\u202eanaD\u200b  Director")
    assert models.ReviewComment.objects.get().name == "anaD Director"


def test_team_comments_are_marked(
    role_client: Callable[[Role], Client], link: models.ReviewLink
) -> None:
    # A guest can type anyone's name; only signed-in users get the mark.
    viewer = role_client(Role.VIEWER)
    name = activity.user_name(User.objects.get(role=Role.VIEWER))
    post(Client(), link, name=name, body="Approved, print it")
    page = Client().get(page_url(link)).content.decode()
    assert "Approved, print it" in page and "review-comment__team" not in page
    post(viewer, link, body="Not yet")
    page = Client().get(page_url(link)).content.decode()
    assert page.count("review-comment__team") == 1
    labels = list(models.ChangeSet.objects.values_list("label", flat=True)[:2])
    assert labels == [
        f"Comment on 1m01 v1 by {name}",
        f"Comment on 1m01 v1 by {name} (guest)",
    ]


def test_a_refresh_gets_nothing_while_nothing_changed(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    client = Client()
    page = client.get(comments_url(link), {"v": "v1"}).content.decode()
    stamp = page.split('data-stamp="')[1].split('"')[0]
    same = client.get(comments_url(link), {"v": "v1", "stamp": stamp})
    assert same.status_code == 204 and not same.content
    assert same["Cache-Control"] == "no-store"
    note = review.add_comment(
        cue.versions.get(), name="Dana", body="Hi", frame=None, user=None
    )
    changed = client.get(comments_url(link), {"v": "v1", "stamp": stamp})
    assert changed.status_code == 200 and b"Hi" in changed.content
    # Resolving, and closing comments, change it too.
    stamps = {review.stamp(link, note.version)}
    review.resolve(note, None)
    stamps.add(review.stamp(link, note.version))
    link.comments_open = False
    link.save()
    stamps.add(review.stamp(link, note.version))
    assert len(stamps) == 3 and stamp not in stamps


def test_a_reply_to_a_note_just_deleted_is_refused_cleanly(
    cue: models.Cue, link: models.ReviewLink
) -> None:
    version = cue.versions.get()
    note = review.add_comment(
        version, name="Dana", body="Too busy", frame=1, user=None
    )
    stale = models.ReviewComment.objects.get(pk=note.pk)
    review.delete_comment(note, None)
    with pytest.raises(review.ReviewError, match="isn't there any more"):
        review.add_comment(
            version, name="Pat", body="Hm", frame=None, user=None, parent=stale
        )
    assert not models.ReviewComment.objects.exists()
    # The page is told to drop the reply; a new guest's name is kept.
    response = Client().post(
        comments_url(link),
        {"v": "v1", "name": "Pat", "body": "Hm", "parent": str(note.pk)},
        headers=HX,
    )
    assert "review:reply-gone" in response["HX-Trigger"]
    assert 'id="review-name"' not in response.content.decode()
    ok = Client().post(
        comments_url(link), {"v": "v1", "name": "Pat", "body": "Hm"}, headers=HX
    )
    assert "review:posted" in ok["HX-Trigger"]
    assert 'id="review-name"' in ok.content.decode()


def test_links_are_not_added_in_the_admin(
    role_client: Callable[[Role], Client], link: models.ReviewLink
) -> None:
    admin = role_client(Role.ADMIN)
    # Without a token, a link would break its cue's page.
    assert admin.get("/admin/cues/reviewlink/add/").status_code == 403
    change = admin.get(f"/admin/cues/reviewlink/{link.pk}/change/")
    assert change.status_code == 200
    assert link.token.encode() not in change.content


def test_a_file_link_that_cannot_be_parsed_is_not_linked(
    role_client: Callable[[Role], Client], cue: models.Cue
) -> None:
    cue.versions.update(file_link="https://[bad/1m01.mp4")
    page = role_client(Role.EDITOR).get(panel_url(cue))
    assert page.status_code == 200
    assert b"Open the Bounce" not in page.content
