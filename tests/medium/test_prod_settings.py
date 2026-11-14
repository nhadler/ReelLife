"""Production settings pass `check --deploy` and refuse bad config.

Each case runs manage.py in a subprocess because settings are loaded once
per process.
"""

import os
import pathlib
import secrets
import subprocess  # noqa: S404
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _prod_env(tmp_path: pathlib.Path, **overrides: str) -> dict[str, str]:
    env = {
        "PATH": os.environ["PATH"],
        "DJANGO_SETTINGS_MODULE": "reeltime.settings.prod",
        "REELTIME_SECRET_KEY": secrets.token_urlsafe(50),
        "REELTIME_ALLOWED_HOSTS": "reeltime.example.com",
        "REELTIME_BASE_URL": "https://reeltime.example.com",
        "REELTIME_DATA_DIR": str(tmp_path),
        "OIDC_ISSUER": "https://id.example.com",
        "OIDC_CLIENT_ID": "reeltime",
        "OIDC_CLIENT_SECRET": secrets.token_urlsafe(32),
    }
    env.update(overrides)
    return env


def _manage(
    env: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "manage.py", *args],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_check_deploy_has_no_warnings(tmp_path: pathlib.Path) -> None:
    result = _manage(
        _prod_env(tmp_path), "check", "--deploy", "--fail-level", "WARNING"
    )
    assert result.returncode == 0, result.stderr


def test_secret_key_can_come_from_file(tmp_path: pathlib.Path) -> None:
    secret = tmp_path / "secret_key"
    secret.write_text(secrets.token_urlsafe(50), encoding="utf-8")
    env = _prod_env(tmp_path, REELTIME_SECRET_KEY_FILE=str(secret))
    del env["REELTIME_SECRET_KEY"]
    result = _manage(env, "check", "--deploy", "--fail-level", "WARNING")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "missing",
    [
        "REELTIME_SECRET_KEY",
        "REELTIME_ALLOWED_HOSTS",
        "REELTIME_BASE_URL",
        "OIDC_ISSUER",
        "OIDC_CLIENT_ID",
        "OIDC_CLIENT_SECRET",
    ],
)
def test_required_settings_must_be_set(
    tmp_path: pathlib.Path, missing: str
) -> None:
    env = _prod_env(tmp_path)
    del env[missing]
    result = _manage(env, "check")
    assert result.returncode != 0
    assert f"{missing} is required" in result.stderr


def test_base_url_must_be_https(tmp_path: pathlib.Path) -> None:
    env = _prod_env(tmp_path, REELTIME_BASE_URL="http://reeltime.example.com")
    result = _manage(env, "check")
    assert result.returncode != 0
    assert "must be an https:// URL" in result.stderr


def test_oidc_issuer_must_be_https(tmp_path: pathlib.Path) -> None:
    env = _prod_env(tmp_path, OIDC_ISSUER="http://id.example.com")
    result = _manage(env, "check")
    assert result.returncode != 0
    assert "OIDC_ISSUER must be an https:// URL" in result.stderr


def test_trusted_proxies_are_checked_at_start(tmp_path: pathlib.Path) -> None:
    good = _prod_env(
        tmp_path,
        REELTIME_TRUST_PROXY="true",
        REELTIME_TRUSTED_PROXIES="192.168.1.35, 172.18.0.0/16, ::1",
    )
    assert _manage(good, "check").returncode == 0
    bad = _prod_env(
        tmp_path,
        REELTIME_TRUST_PROXY="true",
        REELTIME_TRUSTED_PROXIES="caddy",
    )
    result = _manage(bad, "check")
    assert result.returncode != 0
    assert "REELTIME_TRUSTED_PROXIES: 'caddy' isn't an address" in result.stderr
    # A list with trust off would trust no proxy at all: say so.
    off = _prod_env(tmp_path, REELTIME_TRUSTED_PROXIES="192.168.1.35")
    result = _manage(off, "check")
    assert result.returncode != 0
    assert "REELTIME_TRUST_PROXY isn't true" in result.stderr
