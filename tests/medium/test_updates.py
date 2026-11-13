"""The profile menu's version, and the check for a newer release."""

from collections.abc import Callable, Iterator
from importlib import metadata
import io
from typing import Any

from django.core.cache import cache
from django.core.management import call_command
from django.db import DatabaseError
from django.test import Client
from django.test import RequestFactory
from django.test import override_settings
import pytest
import requests
import responses

from accounts.models import Role
from reeltime import context_processors
from reeltime import maintenance
from reeltime import updates

API = "https://api.github.com/repos/nhadler/reeltime/releases/latest"
PAGE = "https://github.com/nhadler/ReelTime/releases/tag/v9.0.0"

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    cache.delete(updates.CACHE_KEY)
    updates.installed.cache_clear()
    yield
    cache.delete(updates.CACHE_KEY)
    updates.installed.cache_clear()


@pytest.fixture(name="github")
def fixture_github() -> Iterator[responses.RequestsMock]:
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield mock


def _release(github: responses.RequestsMock, **body: Any) -> None:
    github.get(API, json={"tag_name": "v9.0.0", "html_url": PAGE, **body})


def _installed(monkeypatch: pytest.MonkeyPatch, version: str) -> None:
    monkeypatch.setattr(metadata, "version", lambda name: version)
    updates.installed.cache_clear()


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("1.2.3", (1, 2, 3)),
        ("v10.0.1", (10, 0, 1)),
        ("1.2", None),
        ("1.2.3-rc.1", None),
        ("development", None),
    ],
)
def test_parse(given: str, expected: tuple[int, int, int] | None) -> None:
    assert updates.parse(given) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://github.com/nhadler/reeltime", API),
        (
            "https://github.com/someone/fork.git/",
            API.replace("nhadler/reeltime", "someone/fork"),
        ),
        ("https://gitlab.com/nhadler/reeltime", None),
        ("http://github.com/nhadler/reeltime", None),
        ("https://github.com/nhadler", None),
    ],
)
def test_api_url(source: str, expected: str | None) -> None:
    assert updates.api_url(source) == expected


def test_installed_without_a_package(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> str:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "version", missing)
    assert updates.installed() == "development"


@override_settings(UPDATE_CHECK=True)
def test_check_remembers_a_newer_release(
    github: responses.RequestsMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _installed(monkeypatch, "1.0.0")
    _release(github)
    assert updates.check() == updates.Release("9.0.0", PAGE)
    assert github.calls[0].request.headers["User-Agent"] == "ReelTime/1.0.0"
    assert updates.available() == updates.Release("9.0.0", PAGE)
    _installed(monkeypatch, "9.0.0")  # once installed, nothing to show
    assert updates.available() is None


@override_settings(UPDATE_CHECK=True)
@pytest.mark.parametrize(
    "reply",
    [
        {"status": 404},
        {"body": requests.ConnectionError("down")},
        {"body": b"not json"},
        {"json": {"tag_name": "nightly", "html_url": PAGE}},
        {"json": {"tag_name": "v9.0.0", "html_url": "https://evil.example/"}},
        {"json": {"tag_name": 9, "html_url": PAGE}},
        {"json": {"name": "no tag"}},
        {"body": b"x" * (updates.MAX_BYTES + 1)},
    ],
)
def test_a_failed_check_keeps_the_last_answer(
    github: responses.RequestsMock, reply: dict[str, Any]
) -> None:
    cache.set(updates.CACHE_KEY, {"version": "2.0.0", "url": PAGE})
    github.get(API, **reply)
    assert updates.check() is None
    assert cache.get(updates.CACHE_KEY) == {"version": "2.0.0", "url": PAGE}


@override_settings(
    UPDATE_CHECK=True, REELTIME_SOURCE_URL="https://example.com/src"
)
def test_no_check_for_source_off_github(github: responses.RequestsMock) -> None:
    assert updates.check() is None
    assert not github.calls


def test_off_means_no_check_and_nothing_shown(
    github: responses.RequestsMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _installed(monkeypatch, "1.0.0")
    cache.set(updates.CACHE_KEY, {"version": "9.0.0", "url": PAGE})
    assert updates.check() is None
    assert updates.available() is None
    assert not github.calls


@override_settings(UPDATE_CHECK=True)
def test_nothing_to_compare(monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch, "1.0.0")
    assert updates.available() is None  # nothing remembered yet
    cache.set(updates.CACHE_KEY, {"version": "junk"})
    assert updates.available() is None
    _installed(monkeypatch, "development")
    cache.set(updates.CACHE_KEY, {"version": "9.0.0", "url": PAGE})
    assert updates.available() is None


@override_settings(UPDATE_CHECK=True)
def test_the_menu_tells_only_admins(
    role_client: Callable[[Role], Client], monkeypatch: pytest.MonkeyPatch
) -> None:
    _installed(monkeypatch, "1.0.0")
    editor, admin = role_client(Role.EDITOR), role_client(Role.ADMIN)
    for client in (editor, admin):
        page = client.get("/").content.decode()
        assert '<p class="profile__version">Version 1.0.0</p>' in page
        assert "profile__button--update" not in page
    cache.set(updates.CACHE_KEY, {"version": "9.0.0", "url": PAGE})
    page = editor.get("/").content.decode()
    assert '<p class="profile__version">Version 1.0.0</p>' in page
    page = admin.get("/").content.decode()
    assert (
        f'class="profile__version profile__version--update" href="{PAGE}"'
        in page
    )
    assert "<span>Update to 9.0.0</span>" in page
    assert "profile__button profile__button--update" in page
    assert "update available" in page


def test_error_pages_render_without_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> None:
        raise DatabaseError("no cache table")

    monkeypatch.setattr(updates, "available", broken)
    request = RequestFactory().get("/")
    request.user = type("Admin", (), {"role": Role.ADMIN})()
    context = context_processors.version(request)
    assert context["update"] is None and context["app_version"]


def test_the_scheduler_checks_when_it_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checks: list[str] = []
    monkeypatch.setattr(updates, "check", lambda: checks.append("check"))
    monkeypatch.setattr(maintenance, "run_forever", lambda **kwargs: None)
    call_command("run_scheduler", stdout=io.StringIO())
    assert checks == ["check"]
