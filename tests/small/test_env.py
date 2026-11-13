"""Tests for reeltime.env."""

import pathlib

from django.core.exceptions import ImproperlyConfigured
import pytest

from reeltime import env


def test_get_reads_variable() -> None:
    assert env.get("A", environ={"A": "1"}) == "1"


def test_get_reads_file(tmp_path: pathlib.Path) -> None:
    secret = tmp_path / "secret"
    secret.write_text("s3cret\n", encoding="utf-8")
    assert env.get("A", environ={"A_FILE": str(secret)}) == "s3cret"


def test_get_refuses_variable_and_file() -> None:
    with pytest.raises(ImproperlyConfigured, match="not both"):
        env.get("A", environ={"A": "1", "A_FILE": "/x"})


def test_get_uses_default() -> None:
    assert env.get("A", "d", environ={}) == "d"


def test_get_requires_value_without_default() -> None:
    with pytest.raises(ImproperlyConfigured, match="A is required"):
        env.get("A", environ={})


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
def test_get_bool_true(value: str) -> None:
    assert env.get_bool("A", default=False, environ={"A": value}) is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off", ""])
def test_get_bool_false(value: str) -> None:
    assert env.get_bool("A", default=True, environ={"A": value}) is False


def test_get_bool_default() -> None:
    assert env.get_bool("A", default=True, environ={}) is True


def test_get_bool_rejects_other_values() -> None:
    with pytest.raises(ImproperlyConfigured, match="true or false"):
        env.get_bool("A", default=False, environ={"A": "maybe"})


def test_get_list_splits_and_trims() -> None:
    environ = {"A": " a.example, ,b.example "}
    assert env.get_list("A", environ=environ) == ["a.example", "b.example"]


def test_get_time_zone_reads_name() -> None:
    environ = {"TZ_SETTING": " America/Los_Angeles "}
    assert env.get_time_zone("TZ_SETTING", "UTC", environ=environ) == (
        "America/Los_Angeles"
    )


def test_get_time_zone_uses_default() -> None:
    assert env.get_time_zone("TZ_SETTING", "UTC", environ={}) == "UTC"


@pytest.mark.parametrize(
    "value", ["Pacific Time", "Mars/Olympus", "../etc", ""]
)
def test_get_time_zone_refuses_unknown_zones(value: str) -> None:
    with pytest.raises(ImproperlyConfigured, match="IANA time zone"):
        env.get_time_zone("TZ_SETTING", "UTC", environ={"TZ_SETTING": value})
