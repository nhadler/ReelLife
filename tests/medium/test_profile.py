"""The profile menu and pictures from the identity provider."""

import io
from typing import Any

from django.test import Client
from django.test import override_settings
from PIL import Image
import pytest
import requests
import responses

from accounts import models
from accounts import pictures
from tests.fakes import oidc_provider
from tests.medium.conftest import SignIn

pytestmark = pytest.mark.django_db

COMPOSER = oidc_provider.DEV_USERS["composer"]
VIEWER = oidc_provider.DEV_USERS["viewer"]


def _user(fake: oidc_provider.FakeUser) -> models.User:
    return models.User.objects.get(oidc_sub=fake.sub)


def test_sign_in_keeps_a_small_copy_of_the_picture(
    sign_in: SignIn, client: Client
) -> None:
    sign_in(client, COMPOSER)
    user = _user(COMPOSER)
    with Image.open(io.BytesIO(bytes(user.picture))) as image:
        assert image.format == "PNG"
        assert image.size == (pictures.SIZE, pictures.SIZE)
    assert len(user.picture_hash) == 16
    reply = client.get("/auth/picture/")
    assert reply.status_code == 200 and reply["Content-Type"] == "image/png"
    assert "immutable" in reply["Cache-Control"]
    page = client.get("/").content.decode()
    assert f'src="/auth/picture/?v={user.picture_hash}"' in page
    assert ">Sign Out</button>" in page
    assert 'href="https://id.example.test/settings/account"' in page


def test_no_picture_shows_initials(sign_in: SignIn, client: Client) -> None:
    sign_in(client, VIEWER)
    assert not _user(VIEWER).picture
    assert client.get("/auth/picture/").status_code == 404
    page = client.get("/").content.decode()
    assert "avatar--initials" in page and ">VV</span>" in page
    assert '<span class="profile__role">Viewer</span>' in page


def test_a_picture_elsewhere_is_never_fetched(
    sign_in: SignIn, client: Client, oidc: oidc_provider.FakeProvider
) -> None:
    oidc.userinfo_overrides["picture"] = "https://elsewhere.example/me.png"
    sign_in(client, COMPOSER)  # responses refuses any unmocked request
    assert not _user(COMPOSER).picture


@override_settings(OIDC_ACCOUNT_URL="https://accounts.example/me")
def test_account_link_can_point_elsewhere(
    sign_in: SignIn, client: Client
) -> None:
    sign_in(client, COMPOSER)
    page = client.get("/").content.decode()
    assert 'href="https://accounts.example/me"' in page


def test_a_user_from_elsewhere_has_no_account_link() -> None:
    # Such as the break-glass admin: no OIDC subject.
    user = models.User.objects.create(username="admin", role=models.Role.EDITOR)
    client = Client()
    client.force_login(user, backend="accounts.oidc.Backend")
    page = client.get("/").content.decode()
    assert "Manage Account" not in page and ">A</span>" in page
    assert client.get("/auth/picture/").status_code == 404


@pytest.mark.parametrize(
    ("first", "last", "username", "expected"),
    [
        ("Casey", "Composer", "x", "CC"),
        ("Casey Jo", "", "x", "CJ"),
        ("Cher", "", "x", "C"),
        ("", "", "nick", "N"),
        ("", "", "", "?"),
    ],
)
def test_initials(first: str, last: str, username: str, expected: str) -> None:
    user = models.User(first_name=first, last_name=last, username=username)
    assert user.initials == expected


# What goes wrong when fetching keeps the picture the user had.

URL = "https://id.example.test/api/users/1/profile-picture.png"


def _png(size: tuple[int, int] = (10, 20)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, "#123456").save(out, "PNG")
    return out.getvalue()


@pytest.fixture(name="had_one")
def fixture_had_one() -> models.User:
    return models.User(username="u", picture=b"old", picture_hash="h")


@pytest.mark.parametrize(
    "reply",
    [
        {"status": 404},
        {"status": 302, "headers": {"Location": "http://10.0.0.1/"}},
        {"body": b"x" * (pictures.MAX_BYTES + 1)},
        {"body": b"<svg/>"},
        {"body": requests.ConnectionError("down")},
    ],
)
def test_failures_keep_the_old_picture(
    had_one: models.User, reply: dict[str, Any]
) -> None:
    with responses.RequestsMock() as mock:
        mock.add(responses.GET, URL, **reply)
        pictures.refresh(had_one, URL)
    assert had_one.picture == b"old" and had_one.picture_hash == "h"


def test_huge_images_are_refused(
    had_one: models.User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pictures, "MAX_PIXELS", 199)
    with responses.RequestsMock() as mock:
        mock.add(responses.GET, URL, body=_png())
        pictures.refresh(had_one, URL)
    assert had_one.picture == b"old"


def test_a_picture_is_cropped_square(had_one: models.User) -> None:
    with responses.RequestsMock() as mock:
        mock.add(responses.GET, URL, body=_png())
        pictures.refresh(had_one, URL)
    with Image.open(io.BytesIO(had_one.picture)) as image:
        assert image.size == (pictures.SIZE, pictures.SIZE)


@pytest.mark.parametrize("claim", [None, 7, "", "http://id.example.test/p.png"])
def test_no_usable_claim_clears_it(had_one: models.User, claim: Any) -> None:
    pictures.refresh(had_one, claim)
    assert had_one.picture == b"" and had_one.picture_hash == ""
