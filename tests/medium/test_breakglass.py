"""Break-glass local admin sign-in."""

import io
import re
from typing import Any

from django.core.management import CommandError
from django.core.management import call_command
from django.test import Client
from django.test import override_settings
from django_otp import oath
from django_otp.plugins.otp_totp.models import TOTPDevice
import pytest

from accounts import breakglass
from accounts import models

pytestmark = pytest.mark.django_db

URL = "/auth/local/"
enabled = override_settings(BREAKGLASS_ENABLED=True)


def _create(*args: str) -> str:
    """Runs create_breakglass and returns the password it printed."""
    out = io.StringIO()
    call_command("create_breakglass", *args, stdout=out)
    match = re.search(r"password: (\S+)", out.getvalue())
    assert match, out.getvalue()
    assert "otpauth://totp/" in out.getvalue()
    return match[1]


def _code() -> str:
    device = TOTPDevice.objects.get(user__username=breakglass.USERNAME)
    value = oath.totp(device.bin_key, device.step, device.t0, device.digits)
    return f"{value:0{device.digits}d}"


def _wrong_code() -> str:
    return f"{(int(_code()) + 1) % 1_000_000:06d}"


def _post(client: Client, password: str, code: str) -> Any:
    return client.post(
        URL,
        {"username": "breakglass", "password": password, "otp_token": code},
    )


def _actions() -> list[str]:
    return list(
        models.AuditEvent.objects.order_by("id").values_list(
            "action", flat=True
        )
    )


def test_disabled_by_default(client: Client) -> None:
    password = _create()
    assert client.get(URL).status_code == 404
    assert _post(client, password, _code()).status_code == 404
    assert b"Emergency" not in client.get("/auth/login/").content


@enabled
def test_password_and_code_sign_in(client: Client) -> None:
    password = _create()
    assert b"Emergency Local Sign-In" in client.get("/auth/login/").content
    response = _post(client, password, _code())
    assert response.status_code == 302
    assert client.get("/admin/").status_code == 200
    event = models.AuditEvent.objects.get()
    assert event.action == "breakglass_login"
    assert event.actor == "breakglass"


@enabled
@pytest.mark.parametrize("what", ["password", "code", "both"])
def test_wrong_credentials_fail(client: Client, what: str) -> None:
    password = _create()
    code = _wrong_code() if what in ("code", "both") else _code()
    if what in ("password", "both"):
        password += "x"
    response = _post(client, password, code)
    assert response.status_code == 400
    assert b"didn't work" in response.content
    assert client.get("/").status_code == 302
    assert _actions() == ["breakglass_failed"]


@enabled
def test_code_is_required(client: Client) -> None:
    password = _create()
    response = client.post(
        URL, {"username": "breakglass", "password": password}
    )
    assert response.status_code == 400
    assert client.get("/").status_code == 302


@enabled
def test_a_code_works_only_once(client: Client) -> None:
    password = _create()
    code = _code()
    assert _post(client, password, code).status_code == 302
    client.post("/auth/logout/")
    assert _post(client, password, code).status_code == 400


@enabled
def test_oidc_users_cannot_use_break_glass(client: Client) -> None:
    _create()
    user = models.User.objects.create(
        username="oidc-x", oidc_sub="x", role=models.Role.ADMIN
    )
    user.set_password("a-long-password-for-testing")
    user.save()
    TOTPDevice.objects.create(user=user, name="t", confirmed=True)
    response = client.post(
        URL,
        {
            "username": "oidc-x",
            "password": "a-long-password-for-testing",
            "otp_token": "000000",
        },
    )
    assert response.status_code == 400


@enabled
def test_demoted_local_user_cannot_sign_in(client: Client) -> None:
    password = _create()
    models.User.objects.filter(username="breakglass").update(role="editor")
    assert _post(client, password, _code()).status_code == 400


@enabled
def test_disabling_ends_existing_sessions(client: Client) -> None:
    password = _create()
    _post(client, password, _code())
    with override_settings(BREAKGLASS_ENABLED=False):
        assert client.get("/").status_code == 302


@enabled
@override_settings(RATELIMIT_BREAKGLASS="2/h")
def test_attempts_are_strictly_limited(client: Client) -> None:
    password = _create()
    for _ in range(2):
        assert _post(client, password, _wrong_code()).status_code == 400
    assert _post(client, password, _code()).status_code == 429


def test_password_is_hashed_with_argon2() -> None:
    _create()
    user = models.User.objects.get(username="breakglass")
    assert user.password.startswith("argon2")
    assert user.role == models.Role.ADMIN
    assert user.oidc_sub is None


def test_command_refuses_to_overwrite_without_reset() -> None:
    _create()
    with pytest.raises(CommandError, match="--reset"):
        _create()


@enabled
def test_reset_replaces_password_and_device(client: Client) -> None:
    old_password = _create()
    old_key = TOTPDevice.objects.get().key
    new_password = _create("--reset")
    assert TOTPDevice.objects.get().key != old_key
    assert _post(client, old_password, _code()).status_code == 400
    assert _post(client, new_password, _code()).status_code == 302


def test_backend_ignores_other_credentials() -> None:
    backend = breakglass.Backend()
    assert backend.authenticate(None, username="breakglass") is None


@enabled
def test_form_has_labelled_fields(client: Client) -> None:
    response = client.get(URL)
    assert response.status_code == 200
    for field in ("username", "password", "otp_token"):
        assert f'<label for="id_{field}">'.encode() in response.content
