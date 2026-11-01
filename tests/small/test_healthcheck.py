"""Tests for the container health probe."""

from collections.abc import Iterator
import http.server
import threading
from unittest import mock

import pytest

from reellife import healthcheck


@pytest.mark.parametrize(
    ("allowed", "host"),
    [
        ("reellife.example.com,other", "reellife.example.com"),
        (" .example.com", "example.com"),
        ("*, reellife.ts.net", "reellife.ts.net"),
        ("", "localhost"),
    ],
)
def test_probe_host(allowed: str, host: str) -> None:
    assert healthcheck.probe_host(allowed) == host


class _Handler(http.server.BaseHTTPRequestHandler):
    body = b"ok"

    def do_GET(self) -> None:  # noqa: N802 pylint: disable=invalid-name
        ok = self.path == "/healthz" and self.headers["Host"] == "app.test"
        self.send_response(200 if ok else 400)
        self.end_headers()
        self.wfile.write(self.body if ok else b"bad")

    def log_message(self, *args: object) -> None:
        del args


@pytest.fixture(name="port")
def fixture_port() -> Iterator[int]:
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def test_check_passes_on_ok(port: int) -> None:
    assert healthcheck.check("app.test", port)


def test_check_fails_on_wrong_host(port: int) -> None:
    assert not healthcheck.check("other.test", port)


def test_check_fails_when_nothing_listens() -> None:
    assert not healthcheck.check("app.test", 1)


def test_main_exit_codes() -> None:
    env = {"REELLIFE_ALLOWED_HOSTS": "app.test"}
    with (
        mock.patch.dict("os.environ", env),
        mock.patch.object(healthcheck, "check", return_value=True) as check,
    ):
        assert healthcheck.main() == 0
    check.assert_called_once_with("app.test")
    with mock.patch.object(healthcheck, "check", return_value=False):
        assert healthcheck.main() == 1
