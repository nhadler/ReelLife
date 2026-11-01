"""Uploading, replacing, serving and removing thumbnails."""

from collections.abc import Callable
import io
import pathlib
from typing import Any

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image
import pytest

from accounts.models import Role
from cues import models
from cues.services import history
from cues.services import projects
from cues.services import thumbnails
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _media(settings: Any, tmp_path: pathlib.Path) -> None:
    settings.DATA_DIR = tmp_path


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


def _png(name: str = "still.png") -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (320, 180), (10, 120, 60)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def _general(project: models.Project, **extra: Any) -> dict[str, Any]:
    return {
        "title": project.title,
        "kind": "film",
        "frame_rate": project.frame_rate,
        "picture_type": "reels",
        "lanes": "2",
        "cue_number_pattern": project.cue_number_pattern,
        **extra,
    }


def _files() -> list[str]:
    folder = pathlib.Path(thumbnails.storage().location) / "thumbnails"
    return (
        sorted(p.name for p in folder.glob("*.webp")) if folder.exists() else []
    )


def test_upload_in_settings_then_serve(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    url = reverse("cues:settings", args=[project.pk])
    editor.post(url, _general(project, thumbnail_upload=_png()))
    project.refresh_from_db()
    assert project.thumbnail_name.endswith(".webp")
    assert _files() == [project.thumbnail_name]

    served = editor.get(
        reverse("cues:thumbnail", args=[project.pk, project.thumbnail_name])
    )
    assert served.status_code == 200
    assert served["Content-Type"] == "image/webp"
    assert "immutable" in served["Cache-Control"]
    data = b"".join(served.streaming_content)  # type: ignore[attr-defined]
    assert Image.open(io.BytesIO(data)).size == (640, 360)

    dashboard = editor.get(reverse("cues:dashboard")).content.decode()
    assert project.thumbnail_name in dashboard


def test_replacing_deletes_the_old_file(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    url = reverse("cues:settings", args=[project.pk])
    editor.post(url, _general(project, thumbnail_upload=_png()))
    project.refresh_from_db()
    first = project.thumbnail_name
    editor.post(url, _general(project, thumbnail_upload=_png("other.png")))
    project.refresh_from_db()
    assert project.thumbnail_name != first
    assert _files() == [project.thumbnail_name]
    old = editor.get(reverse("cues:thumbnail", args=[project.pk, first]))
    assert old.status_code == 404


def test_removing_the_thumbnail(editor: Client) -> None:
    project = factories.ProjectFactory.create(production_number="LH1")
    url = reverse("cues:settings", args=[project.pk])
    editor.post(url, _general(project, thumbnail_upload=_png()))
    assert "Remove the Thumbnail" in editor.get(url).content.decode()
    editor.post(url, _general(project, remove_thumbnail="on"))
    project.refresh_from_db()
    assert not project.thumbnail
    assert _files() == []
    assert (
        "thumb--empty" in editor.get(reverse("cues:dashboard")).content.decode()
    )


def test_bad_uploads_are_field_errors(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    url = reverse("cues:settings", args=[project.pk])
    bad = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
    response = editor.post(url, _general(project, thumbnail_upload=bad))
    assert response.status_code == 200
    assert "That file isn&#x27;t a readable image." in response.content.decode()
    project.refresh_from_db()
    assert not project.thumbnail


def test_upload_when_creating_a_project(editor: Client) -> None:
    data = {
        "title": "With Still",
        "kind": "film",
        "frame_rate": "24",
        "picture_type": "reels",
        "lanes": "2",
        "cue_number_pattern": r"^\d+m\d+$",
        "reels-TOTAL_FORMS": "0",
        "reels-INITIAL_FORMS": "0",
        "thumbnail_upload": _png(),
    }
    editor.post(reverse("cues:project_create"), data)
    project = models.Project.objects.get(title="With Still")
    assert project.thumbnail


def test_serving_rules(
    editor: Client, client: Client, role_client: Callable[[Role], Client]
) -> None:
    project = factories.ProjectFactory.create()
    none = editor.get(reverse("cues:thumbnail", args=[project.pk, "x.webp"]))
    assert none.status_code == 404
    projects.set_thumbnail(project, thumbnails.save_upload(_png()))
    url = reverse("cues:thumbnail", args=[project.pk, project.thumbnail_name])
    assert role_client(Role.VIEWER).get(url).status_code == 200
    assert client.get(url).status_code == 302  # signed out: to sign-in


def test_deleting_a_project_deletes_its_file() -> None:
    project = factories.ProjectFactory.create()
    projects.set_thumbnail(project, thumbnails.save_upload(_png()))
    assert _files()
    project.delete()
    assert _files() == []


def test_projects_without_thumbnails_delete_cleanly() -> None:
    factories.ProjectFactory.create().delete()


# A cue's thumbnail (the Thumbnail column; Oct 31)


def test_cue_thumbnail_upload_serve_undo_and_remove(
    editor: Client, role_client: Callable[[Role], Client]
) -> None:
    cue = factories.CueFactory.create()
    upload_url = reverse("cues:cue_thumbnail_upload", args=[cue.pk])
    response = editor.post(upload_url, {"thumbnail": _png()})
    assert response["Location"] == reverse("cues:cue", args=[cue.pk])
    cue.refresh_from_db()
    first = cue.thumbnail_name
    assert first.endswith(".webp")
    url = reverse("cues:cue_thumbnail", args=[cue.pk, first])
    served = role_client(Role.VIEWER).get(url)
    assert served["Content-Type"] == "image/webp"
    assert "immutable" in served["Cache-Control"]
    stale = reverse("cues:cue_thumbnail", args=[cue.pk, "old.webp"])
    assert editor.get(stale).status_code == 404
    page = editor.get(reverse("cues:cue", args=[cue.pk])).content.decode()
    assert url in page and ">Remove Still</button>" in page

    editor.post(upload_url, {"thumbnail": _png("second.png")})
    cue.refresh_from_db()
    assert cue.thumbnail_name != first
    # Undo puts the first back: replaced files are kept for this.
    history.undo(
        models.ChangeSet.objects.filter(project=cue.project).latest("pk")
    )
    cue.refresh_from_db()
    assert cue.thumbnail_name == first
    assert editor.get(url).status_code == 200

    editor.post(upload_url, {"remove": "1"})
    cue.refresh_from_db()
    assert not cue.thumbnail
    assert editor.get(url).status_code == 404
    editor.post(upload_url, {"remove": "1"})  # nothing to remove: fine


def test_cue_thumbnail_refusals(
    editor: Client, role_client: Callable[[Role], Client]
) -> None:
    cue = factories.CueFactory.create()
    upload_url = reverse("cues:cue_thumbnail_upload", args=[cue.pk])
    viewer = role_client(Role.VIEWER)
    assert viewer.post(upload_url, {"thumbnail": _png()}).status_code == 403
    response = editor.post(upload_url, {}, follow=True)
    assert "Choose an image to upload." in response.content.decode()
    bad = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
    response = editor.post(upload_url, {"thumbnail": bad}, follow=True)
    assert "isn&#x27;t a readable image" in response.content.decode()
    cue.refresh_from_db()
    assert not cue.thumbnail


def test_cues_without_one_show_the_projects_thumbnail(editor: Client) -> None:
    """Nick, Oct 31: inherit the project thumbnail, and let it be off."""
    cue = factories.CueFactory.create()
    project = cue.project
    url = reverse("cues:settings", args=[project.pk])
    editor.post(
        url,
        _general(project, thumbnail_upload=_png(), cue_thumbnail_fallback="on"),
    )
    project.refresh_from_db()
    project_url = reverse(
        "cues:thumbnail", args=[project.pk, project.thumbnail_name]
    )
    grid_url = reverse("cues:api_grid", args=[project.pk])
    row = editor.get(grid_url).json()["rows"][0]
    assert (row["thumbnail"], row["_thumbnail_inherited"]) == (
        project_url,
        True,
    )
    page = editor.get(reverse("cues:cue", args=[cue.pk])).content.decode()
    assert 'alt="The project\'s thumbnail"' in page
    assert "cue-thumb--inherited" in page

    # The cue's own wins.
    editor.post(
        reverse("cues:cue_thumbnail_upload", args=[cue.pk]),
        {"thumbnail": _png()},
    )
    row = editor.get(grid_url).json()["rows"][0]
    assert row["thumbnail"].startswith(f"/cues/{cue.pk}/thumbnail/")
    assert row["_thumbnail_inherited"] is False

    # Turned off, a cue without one shows the empty frame.
    editor.post(
        reverse("cues:cue_thumbnail_upload", args=[cue.pk]), {"remove": "1"}
    )
    editor.post(url, _general(project))  # the box unticked
    project.refresh_from_db()
    assert project.cue_thumbnail_fallback is False
    row = editor.get(grid_url).json()["rows"][0]
    assert (row["thumbnail"], row["_thumbnail_inherited"]) == ("", False)


def test_the_setting_is_on_both_project_pages(editor: Client) -> None:
    project = factories.ProjectFactory.create()
    assert project.cue_thumbnail_fallback is True  # on by default
    for url in (
        reverse("cues:project_create"),
        reverse("cues:settings", args=[project.pk]),
    ):
        html = editor.get(url).content.decode()
        assert 'name="cue_thumbnail_fallback"' in html
