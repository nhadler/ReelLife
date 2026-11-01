"""Cue-name parsing and numbering shared by importers."""

import pytest

from cues import numbering
from cues.importers import base


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("1m01 Main Title", ("1m01", "Main Title")),
        ("  1m02   The Chase  ", ("1m02", "The Chase")),
        ("1m03", ("1m03", "")),
        ("", ("", "")),
        ("@ {Sc=MT Main Titles}", ("@", "{Sc=MT Main Titles}")),
    ],
)
def test_split_name(label: str, expected: tuple[str, str]) -> None:
    assert base.split_name(label) == expected


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("101m01 Cold Open IN", ("in", "101m01 Cold Open")),
        ("101m01 OUT", ("out", "101m01")),
        ("1m02 Title Card out ", ("out", "1m02 Title Card")),
        ("FFOA", (None, "FFOA")),
        ("IN", (None, "IN")),
        ("Spinning", (None, "Spinning")),
    ],
)
def test_marker_role(label: str, expected: tuple[str | None, str]) -> None:
    assert base.marker_role(label) == expected


@pytest.mark.parametrize(
    ("number", "following"),
    [
        ("1m01", "1m02"),
        ("1m09", "1m10"),
        ("1m99", "1m100"),
        ("1m04a", "1m05"),
        ("101m01", "101m02"),
        ("12", "13"),
    ],
)
def test_next_number(number: str, following: str) -> None:
    assert numbering.next_number(number) == following


def test_next_number_needs_digits() -> None:
    with pytest.raises(ValueError, match="no digits"):
        numbering.next_number("Main")


def test_is_valid_uses_the_pattern_and_survives_bad_ones() -> None:
    assert numbering.is_valid("1m01", r"^\d+m\d+$")
    assert not numbering.is_valid("DX_01", r"^\d+m\d+$")
    assert not numbering.is_valid("1m01", "([")


@pytest.mark.parametrize(
    ("in_picture", "picture", "taken", "expected"),
    [
        (["1m01", "1m02"], "1", set(), "1m03"),
        (["1m2", "1m10", "1m9"], "1", set(), "1m11"),
        (["1m04a"], "1", set(), "1m05"),
        ([], "2", set(), "2m01"),
        ([], "A12", set(), "1m01"),
        (["1m01"], "1", {"1m02", "1m03"}, "1m04"),
        ([], "3", {"3m01"}, "3m02"),
    ],
)
def test_suggest_number(
    in_picture: list[str], picture: str, taken: set[str], expected: str
) -> None:
    assert numbering.suggest_number(in_picture, picture, taken) == expected


@pytest.mark.parametrize(
    ("labels", "minor", "expected"),
    [
        ([], False, "v1"),
        (["v1"], False, "v2"),
        (["v1", "v3", "v2"], False, "v4"),
        (["v1"], True, "v1.1"),
        (["v1", "v1.1"], True, "v1.2"),
        (["v1.2"], False, "v2"),
        (["V2", "final", "mix"], False, "v3"),
        (["final"], True, "v1"),
    ],
)
def test_next_version_label(
    labels: list[str], minor: bool, expected: str
) -> None:
    assert numbering.next_version_label(labels, minor=minor) == expected
