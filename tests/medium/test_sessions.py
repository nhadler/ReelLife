"""Session lifetimes: 8 hours idle, 7 days absolute."""

from collections.abc import Callable
import datetime

from django.test import Client
import freezegun
import pytest

from accounts import oidc
from accounts import sessions
from accounts.models import Role

# Recent, so the test client's session (saved at START) hasn't expired
# by the time a test reads it back outside the frozen clock.
START = datetime.datetime.now(datetime.UTC).replace(
    minute=0, second=0, microsecond=0
)
HOUR = datetime.timedelta(hours=1)


@pytest.fixture(autouse=True)
def _provider_always_agrees(monkeypatch: pytest.MonkeyPatch) -> None:
    # These clients never signed in at a provider; checks with PocketID
    # are tested in test_session_checks.py.
    monkeypatch.setattr(oidc, "check_session", lambda request: True)


def _signed_in(role_client: Callable[[Role], Client]) -> Client:
    with freezegun.freeze_time(START):
        return role_client(Role.EDITOR)


def _status_at(client: Client, when: datetime.datetime) -> int:
    with freezegun.freeze_time(when):
        return client.get("/").status_code


def test_sign_in_stamps_the_session(
    role_client: Callable[[Role], Client],
) -> None:
    client = _signed_in(role_client)
    stamp = int(START.timestamp())
    assert client.session[sessions.AUTH_AT] == stamp
    assert client.session[sessions.SEEN_AT] == stamp


def test_active_session_stays_signed_in(
    role_client: Callable[[Role], Client],
) -> None:
    client = _signed_in(role_client)
    assert _status_at(client, START + 7 * HOUR) == 200
    assert _status_at(client, START + 14 * HOUR) == 200


def test_idle_session_expires_after_8_hours(
    role_client: Callable[[Role], Client],
) -> None:
    client = _signed_in(role_client)
    assert _status_at(client, START + 8 * HOUR + HOUR / 60) == 302
    assert _status_at(client, START + 8 * HOUR + HOUR / 30) == 302


def test_busy_session_expires_after_7_days(
    role_client: Callable[[Role], Client],
) -> None:
    client = _signed_in(role_client)
    when = START
    for _ in range(7 * 4):
        when += 6 * HOUR
        assert _status_at(client, when) == 200
    assert _status_at(client, when + 6 * HOUR) == 302


def test_activity_is_recorded_at_most_once_a_minute(
    role_client: Callable[[Role], Client],
) -> None:
    client = _signed_in(role_client)
    _status_at(client, START + datetime.timedelta(seconds=30))
    assert client.session[sessions.SEEN_AT] == int(START.timestamp())
    _status_at(client, START + datetime.timedelta(seconds=90))
    assert client.session[sessions.SEEN_AT] == int(START.timestamp()) + 90


@pytest.mark.django_db
def test_session_without_stamps_starts_counting(
    role_client: Callable[[Role], Client],
) -> None:
    client = _signed_in(role_client)
    session = client.session
    del session[sessions.AUTH_AT]
    del session[sessions.SEEN_AT]
    session.save()
    assert _status_at(client, START + HOUR) == 200
    assert client.session[sessions.AUTH_AT] == int((START + HOUR).timestamp())
