"""Sign-in through OIDC against the fake provider."""

import time
import urllib.parse

from django.test import Client
import pytest

from accounts import models
from tests.fakes import oidc_provider
from tests.medium.conftest import SignIn

pytestmark = pytest.mark.django_db

COMPOSER = oidc_provider.DEV_USERS["composer"]
NICK = oidc_provider.DEV_USERS["nick"]
VIEWER = oidc_provider.DEV_USERS["viewer"]
OUTSIDER = oidc_provider.DEV_USERS["outsider"]


def _actions() -> list[str]:
    return list(
        models.AuditEvent.objects.order_by("id").values_list(
            "action", flat=True
        )
    )


def _signed_in_user(client: Client) -> models.User | None:
    response = client.get("/")
    if response.status_code != 200:
        return None
    user = response.wsgi_request.user
    assert isinstance(user, models.User)
    return user


def test_editor_signs_in(client: Client, sign_in: SignIn) -> None:
    response = sign_in(client, COMPOSER)
    assert response.status_code == 302
    assert response["Location"] == "/"
    user = models.User.objects.get(oidc_sub=COMPOSER.sub)
    assert user.role == models.Role.EDITOR
    assert user.email == COMPOSER.email
    assert user.first_name == "Casey"
    assert not user.is_staff
    assert not user.has_usable_password()
    assert _signed_in_user(client) == user
    assert _actions() == ["login"]


def test_admin_gets_staff_and_superuser(
    client: Client, sign_in: SignIn
) -> None:
    sign_in(client, NICK)
    user = models.User.objects.get(oidc_sub=NICK.sub)
    assert user.role == models.Role.ADMIN
    assert user.is_staff and user.is_superuser


def test_viewer_signs_in_as_viewer(client: Client, sign_in: SignIn) -> None:
    sign_in(client, VIEWER)
    assert models.User.objects.get(oidc_sub=VIEWER.sub).role == "viewer"


def test_user_in_no_group_is_refused(client: Client, sign_in: SignIn) -> None:
    response = sign_in(client, OUTSIDER)
    assert response.status_code == 403
    assert b"isn't in a ReelTime group" in response.content
    assert not models.User.objects.filter(oidc_sub=OUTSIDER.sub).exists()
    assert _signed_in_user(client) is None
    event = models.AuditEvent.objects.get()
    assert event.action == "login_refused"
    assert event.actor == OUTSIDER.sub
    assert event.detail == {"reason": "no_group"}


@pytest.mark.parametrize("where", ["id_token", "userinfo"])
def test_groups_read_from_either_place(
    client: Client,
    sign_in: SignIn,
    oidc: oidc_provider.FakeProvider,
    where: str,
) -> None:
    oidc.groups_in = where
    sign_in(client, COMPOSER)
    assert models.User.objects.get(oidc_sub=COMPOSER.sub).role == "editor"


def test_users_are_keyed_on_sub_not_email(
    client: Client, sign_in: SignIn
) -> None:
    models.User.objects.create(username="someone", email=COMPOSER.email)
    sign_in(client, COMPOSER)
    assert models.User.objects.filter(email=COMPOSER.email).count() == 2
    assert _signed_in_user(client) == models.User.objects.get(
        oidc_sub=COMPOSER.sub
    )


def test_returning_user_is_updated_and_role_change_audited(
    client: Client, sign_in: SignIn, oidc: oidc_provider.FakeProvider
) -> None:
    sign_in(client, COMPOSER)
    client.logout()
    oidc.userinfo_overrides = {
        "email": "new@example.test",
        "groups": ["reeltime-admins"],
    }
    sign_in(client, COMPOSER)
    user = models.User.objects.get(oidc_sub=COMPOSER.sub)
    assert user.email == "new@example.test"
    assert user.role == models.Role.ADMIN
    change = models.AuditEvent.objects.get(action="role_changed")
    assert change.detail == {"old": "editor", "new": "admin"}


def test_removed_from_groups_is_refused_next_time(
    client: Client, sign_in: SignIn, oidc: oidc_provider.FakeProvider
) -> None:
    sign_in(client, COMPOSER)
    client.logout()
    oidc.userinfo_overrides = {"groups": []}
    assert sign_in(client, COMPOSER).status_code == 403


def test_removal_found_at_sign_in_ends_other_sessions(
    sign_in: SignIn, oidc: oidc_provider.FakeProvider
) -> None:
    laptop, phone = Client(), Client()
    sign_in(laptop, NICK)
    assert laptop.get("/").status_code == 200
    oidc.userinfo_overrides = {"groups": ["some-other-app"]}
    assert sign_in(phone, NICK).status_code == 403
    user = models.User.objects.get(oidc_sub=NICK.sub)
    assert user.role == models.Role.NONE
    assert not user.is_staff and not user.is_superuser
    change = models.AuditEvent.objects.get(action="role_changed")
    assert change.detail == {"old": "admin", "new": "none"}
    assert laptop.get("/").status_code == 403
    # Refused again: nothing more to record.
    assert sign_in(phone, NICK).status_code == 403
    assert models.AuditEvent.objects.filter(action="role_changed").count() == 1
    # Back in a group, they sign in with the new role.
    oidc.userinfo_overrides = {"groups": ["reeltime-viewers"]}
    sign_in(phone, NICK)
    assert models.User.objects.get(oidc_sub=NICK.sub).role == "viewer"


def test_es256_tokens_are_accepted(
    client: Client, sign_in: SignIn, oidc: oidc_provider.FakeProvider
) -> None:
    oidc.signing = "ES256"
    assert sign_in(client, COMPOSER)["Location"] == "/"


@pytest.mark.parametrize("signing", ["none", "HS256", "wrong-key"])
def test_bad_signatures_are_rejected(
    client: Client,
    sign_in: SignIn,
    oidc: oidc_provider.FakeProvider,
    signing: str,
) -> None:
    oidc.signing = signing
    response = sign_in(client, COMPOSER)
    assert response.status_code == 400
    assert _signed_in_user(client) is None
    assert _actions() == ["login_failed"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://evil.example.test"},
        {"aud": "someone-else"},
        {"aud": ["reeltime-test", "someone-else"]},
        {"exp": int(time.time()) - 3600},
        {"exp": None},
        {"nonce": "replayed"},
        {"sub": "a-different-subject"},
    ],
    ids=[
        "wrong-iss",
        "wrong-aud",
        "multi-aud-no-azp",
        "expired",
        "no-exp",
        "nonce",
        "sub-mismatch",
    ],
)
def test_bad_id_token_claims_are_rejected(
    client: Client,
    sign_in: SignIn,
    oidc: oidc_provider.FakeProvider,
    overrides: dict[str, object],
) -> None:
    oidc.id_token_overrides = {
        key: value for key, value in overrides.items() if value is not None
    }
    if "exp" in overrides and overrides["exp"] is None:
        oidc.id_token_overrides["exp"] = "not-a-number"
    response = sign_in(client, COMPOSER)
    assert response.status_code == 400
    assert _signed_in_user(client) is None


def test_multi_audience_with_matching_azp_is_accepted(
    client: Client, sign_in: SignIn, oidc: oidc_provider.FakeProvider
) -> None:
    oidc.id_token_overrides = {
        "aud": ["reeltime-test", "other"],
        "azp": "reeltime-test",
    }
    assert sign_in(client, COMPOSER)["Location"] == "/"


def test_authorization_request_uses_pkce_nonce_and_base_url(
    client: Client, oidc: oidc_provider.FakeProvider
) -> None:
    response = client.get("/auth/oidc/start/")
    location = urllib.parse.urlsplit(response["Location"])
    query = dict(urllib.parse.parse_qsl(location.query))
    assert f"{location.scheme}://{location.netloc}" == oidc.issuer
    assert query["code_challenge_method"] == "S256"
    assert len(query["code_challenge"]) == 43
    assert query["nonce"]
    assert query["scope"] == "openid profile email groups offline_access"
    assert query["redirect_uri"] == "http://testserver/auth/oidc/callback/"


def test_next_url_is_kept(client: Client, sign_in: SignIn) -> None:
    response = sign_in(client, COMPOSER, next_url="/projects/1/")
    assert response["Location"] == "/projects/1/"


def test_offsite_next_url_is_ignored(client: Client, sign_in: SignIn) -> None:
    response = sign_in(client, COMPOSER, next_url="https://evil.test/")
    assert response["Location"] == "/"


def test_provider_error_shows_failure_page(client: Client) -> None:
    response = client.get("/auth/oidc/callback/?error=access_denied")
    assert response.status_code == 400
    assert b"Didn't Complete" in response.content


def test_unsolicited_error_callback_keeps_the_session(
    client: Client, sign_in: SignIn
) -> None:
    sign_in(client, COMPOSER)
    for query in ("error=access_denied", "error=access_denied&state=forged"):
        response = client.get(f"/auth/oidc/callback/?{query}")
        assert response.status_code == 400
        assert _signed_in_user(client) is not None


def test_error_for_this_sessions_sign_in_uses_up_the_state(
    client: Client, sign_in: SignIn
) -> None:
    sign_in(client, COMPOSER)
    start = client.get("/auth/oidc/start/")
    state = dict(
        urllib.parse.parse_qsl(urllib.parse.urlsplit(start["Location"]).query)
    )["state"]
    assert state in client.session["oidc_states"]
    response = client.get(
        f"/auth/oidc/callback/?error=access_denied&state={state}"
    )
    assert response.status_code == 400
    assert state not in client.session.get("oidc_states", {})
    assert _signed_in_user(client) is None


def test_error_for_a_sign_in_from_signed_out_shows_failure(
    client: Client, oidc: oidc_provider.FakeProvider
) -> None:
    del oidc
    start = client.get("/auth/oidc/start/")
    state = dict(
        urllib.parse.parse_qsl(urllib.parse.urlsplit(start["Location"]).query)
    )["state"]
    response = client.get(
        f"/auth/oidc/callback/?error=access_denied&state={state}"
    )
    assert response.status_code == 400
    assert state not in client.session.get("oidc_states", {})


def test_unknown_state_is_rejected(
    client: Client, oidc: oidc_provider.FakeProvider
) -> None:
    del oidc
    client.get("/auth/oidc/start/")
    response = client.get("/auth/oidc/callback/?code=x&state=forged")
    assert response.status_code == 400


def test_provider_down_shows_unavailable_page(
    client: Client, oidc: oidc_provider.FakeProvider, settings: object
) -> None:
    del oidc
    setattr(settings, "OIDC_ISSUER", "https://down.example.test")  # noqa: B010
    response = client.get("/auth/oidc/start/")
    assert response.status_code == 503
    assert b"can't reach PocketID" in response.content


def test_discovery_with_wrong_issuer_is_refused(
    client: Client, oidc: oidc_provider.FakeProvider
) -> None:
    oidc.issuer = "https://id.example.test/other"
    # Endpoints moved with the issuer, but discovery is still served at
    # the configured URL and now claims a different issuer.
    response = client.get("/auth/oidc/start/")
    assert response.status_code == 503


def test_login_page_offers_pocketid(client: Client) -> None:
    response = client.get("/auth/login/?next=/x/")
    assert response.status_code == 200
    assert b"Sign In with PocketID" in response.content
    assert b"/auth/oidc/start/?next=/x/" in response.content


def test_login_page_redirects_signed_in_users(
    client: Client, sign_in: SignIn
) -> None:
    sign_in(client, COMPOSER)
    assert client.get("/auth/login/")["Location"] == "/"


def test_logout_ends_provider_session(
    client: Client, sign_in: SignIn, oidc: oidc_provider.FakeProvider
) -> None:
    sign_in(client, COMPOSER)
    response = client.post("/auth/logout/")
    target = urllib.parse.urlsplit(response["Location"])
    query = dict(urllib.parse.parse_qsl(target.query))
    assert response["Location"].startswith(oidc.url("/api/oidc/end-session"))
    assert query["post_logout_redirect_uri"] == (
        "http://testserver/auth/login/"
    )
    assert query["client_id"] == "reeltime-test"
    assert query["id_token_hint"]
    assert _signed_in_user(client) is None
    assert _actions() == ["login", "logout"]


def test_logout_without_end_session_returns_to_login(
    client: Client, sign_in: SignIn, oidc: oidc_provider.FakeProvider
) -> None:
    oidc.end_session = False
    sign_in(client, COMPOSER)
    assert client.post("/auth/logout/")["Location"] == "/auth/login/"


def test_logout_requires_post(client: Client, sign_in: SignIn) -> None:
    sign_in(client, COMPOSER)
    assert client.get("/auth/logout/").status_code == 405


def test_dashboard_requires_sign_in(client: Client) -> None:
    response = client.get("/")
    assert response.status_code == 302
    assert response["Location"] == "/auth/login/?next=/"
