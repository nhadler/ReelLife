"""Signed-in sessions are re-checked with PocketID."""

import datetime
from typing import Any

from django.test import Client
from django.test import RequestFactory
from django.test import override_settings
import freezegun
import pytest
import requests

from accounts import models

# The `oidc` fixture (the fake provider) would hide the module's name.
from accounts import oidc as accounts_oidc
from accounts import sessions
from tests.fakes import oidc_provider
from tests.medium.conftest import SignIn

pytestmark = pytest.mark.django_db

COMPOSER = oidc_provider.DEV_USERS["composer"]
MINUTE = 60


def _sign_in(client: Client, sign_in: SignIn) -> int:
    """Signs in (on the real clock, which the fake provider uses).

    Returns:
        The sign-in time, in seconds since the epoch.
    """
    assert sign_in(client, COMPOSER).status_code == 302
    return int(client.session[sessions.AUTH_AT])


def _status_at(client: Client, when: int) -> int:
    with freezegun.freeze_time(
        datetime.datetime.fromtimestamp(when, datetime.UTC)
    ):
        return client.get("/").status_code


def _audit() -> list[tuple[str, dict[str, Any]]]:
    return [
        (event.action, event.detail)
        for event in models.AuditEvent.objects.exclude(action="login").order_by(
            "id"
        )
    ]


def test_sessions_are_checked_every_few_minutes(
    client: Client, sign_in: SignIn
) -> None:
    start = _sign_in(client, sign_in)
    assert client.session[sessions.CHECKED_AT] == start
    assert _status_at(client, start + 4 * MINUTE) == 200
    assert client.session[sessions.CHECKED_AT] == start
    assert _status_at(client, start + 5 * MINUTE) == 200
    checked = start + 5 * MINUTE
    assert client.session[sessions.CHECKED_AT] == checked


def test_removal_from_groups_ends_the_session(
    client: Client, sign_in: SignIn, oidc: oidc_provider.FakeProvider
) -> None:
    start = _sign_in(client, sign_in)
    oidc.userinfo_overrides = {"groups": ["some-other-app"]}
    assert _status_at(client, start + 2 * MINUTE) == 200
    assert _status_at(client, start + 6 * MINUTE) == 302
    user = models.User.objects.get(oidc_sub=COMPOSER.sub)
    assert user.role == models.Role.NONE
    assert _audit() == [
        ("role_changed", {"old": "editor", "new": "none"}),
        ("login_refused", {"reason": "session_no_group"}),
    ]


def test_a_new_role_applies_mid_session(
    client: Client, sign_in: SignIn, oidc: oidc_provider.FakeProvider
) -> None:
    start = _sign_in(client, sign_in)
    oidc.userinfo_overrides = {"groups": ["reellife-admins"]}
    assert _status_at(client, start + 6 * MINUTE) == 200
    assert models.User.objects.get(oidc_sub=COMPOSER.sub).role == "admin"
    assert _audit() == [("role_changed", {"old": "editor", "new": "admin"})]


def test_groups_only_in_the_id_token_keep_the_session(
    client: Client, sign_in: SignIn, oidc: oidc_provider.FakeProvider
) -> None:
    oidc.groups_in = "id_token"
    start = _sign_in(client, sign_in)
    assert _status_at(client, start + 6 * MINUTE) == 200
    assert models.User.objects.get(oidc_sub=COMPOSER.sub).role == "editor"


def test_an_expired_token_without_refresh_ends_the_session(
    client: Client, sign_in: SignIn, oidc: oidc_provider.FakeProvider
) -> None:
    start = _sign_in(client, sign_in)
    oidc.expire_access_tokens()
    assert _status_at(client, start + 6 * MINUTE) == 302


@override_settings(OIDC_RP_SCOPES="openid profile email groups")
def test_without_offline_access_there_is_no_refresh_token(
    client: Client, sign_in: SignIn, oidc: oidc_provider.FakeProvider
) -> None:
    oidc.issue_refresh_tokens = True
    start = _sign_in(client, sign_in)
    assert accounts_oidc.STORED_REFRESH not in client.session
    oidc.expire_access_tokens()
    assert _status_at(client, start + 6 * MINUTE) == 302


def test_an_expired_token_is_refreshed(
    client: Client, sign_in: SignIn, oidc: oidc_provider.FakeProvider
) -> None:
    oidc.issue_refresh_tokens = True
    start = _sign_in(client, sign_in)
    first = client.session[accounts_oidc.STORED_REFRESH]
    oidc.expire_access_tokens()
    assert _status_at(client, start + 6 * MINUTE) == 200
    # The provider rotated the refresh token; the new one is kept.
    assert client.session[accounts_oidc.STORED_REFRESH] != first
    oidc.expire_access_tokens()
    assert _status_at(client, start + 12 * MINUTE) == 200


def test_a_refresh_token_that_is_not_rotated_is_kept(
    client: Client, sign_in: SignIn, oidc: oidc_provider.FakeProvider
) -> None:
    oidc.issue_refresh_tokens = True
    oidc.rotate_refresh_tokens = False
    start = _sign_in(client, sign_in)
    first = client.session[accounts_oidc.STORED_REFRESH]
    oidc.expire_access_tokens()
    assert _status_at(client, start + 6 * MINUTE) == 200
    assert client.session[accounts_oidc.STORED_REFRESH] == first


def test_revoked_tokens_end_the_session(
    client: Client, sign_in: SignIn, oidc: oidc_provider.FakeProvider
) -> None:
    oidc.issue_refresh_tokens = True
    start = _sign_in(client, sign_in)
    oidc.revoke(COMPOSER)
    assert _status_at(client, start + 6 * MINUTE) == 302


def test_userinfo_for_someone_else_ends_the_session(
    client: Client, sign_in: SignIn, oidc: oidc_provider.FakeProvider
) -> None:
    start = _sign_in(client, sign_in)
    oidc.userinfo_overrides = {"sub": "someone-else"}
    assert _status_at(client, start + 6 * MINUTE) == 302


@pytest.mark.parametrize(
    "answer",
    [(500, {"error": "down"}), (200, ["not", "an", "object"])],
)
def test_a_broken_provider_keeps_the_session(
    client: Client,
    sign_in: SignIn,
    oidc: oidc_provider.FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
    answer: tuple[int, Any],
) -> None:
    start = _sign_in(client, sign_in)
    monkeypatch.setattr(oidc, "userinfo", lambda authorization: answer)
    assert _status_at(client, start + 6 * MINUTE) == 200
    # Postponed, not retried on every request.
    checked = start + 6 * MINUTE
    assert client.session[sessions.CHECKED_AT] == checked


def test_an_unreachable_provider_keeps_the_session(
    client: Client,
    sign_in: SignIn,
    oidc: oidc_provider.FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = _sign_in(client, sign_in)

    def down(authorization: str | None) -> Any:
        raise requests.ConnectionError(authorization)

    monkeypatch.setattr(oidc, "userinfo", down)
    assert _status_at(client, start + 6 * MINUTE) == 200


@pytest.mark.parametrize(
    "answer",
    [
        (500, {"error": "down"}),
        (200, ["not", "an", "object"]),
        (200, {"token_type": "Bearer"}),
    ],
)
def test_a_broken_refresh_keeps_the_session(
    client: Client,
    sign_in: SignIn,
    oidc: oidc_provider.FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
    answer: tuple[int, Any],
) -> None:
    oidc.issue_refresh_tokens = True
    start = _sign_in(client, sign_in)
    oidc.expire_access_tokens()
    monkeypatch.setattr(oidc, "token", lambda *args: answer)
    assert _status_at(client, start + 6 * MINUTE) == 200


def test_sessions_from_before_the_checks_sign_in_again(
    client: Client, sign_in: SignIn
) -> None:
    start = _sign_in(client, sign_in)
    session = client.session
    del session[sessions.CHECKED_AT]
    del session[accounts_oidc.STORED_ACCESS]
    session.save()
    assert _status_at(client, start + MINUTE) == 302


def test_stored_id_token_problems_fall_back_to_userinfo(
    client: Client, sign_in: SignIn
) -> None:
    for stored in ("not a token", None):
        start = _sign_in(client, sign_in)
        session = client.session
        session[accounts_oidc.STORED_ID] = stored
        session.save()
        assert _status_at(client, start + 6 * MINUTE) == 200
        client.logout()


def test_break_glass_sessions_are_not_oidc_sessions() -> None:
    request = RequestFactory().get("/")
    request.user = models.User(username="breakglass", role="admin")
    assert accounts_oidc.check_session(request)
