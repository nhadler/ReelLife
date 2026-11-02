"""Cue number helpers for the cue tools."""

import pytest

from cues import numbering


@pytest.mark.parametrize(
    ("number", "by", "expected"),
    [
        ("1m05", 1, "1m06"),
        ("1m05s", 2, "1m07s"),
        ("1m09", 1, "1m10"),
        ("1m99", 1, "1m100"),
        ("1m01", -1, "1m00"),
        ("1m00", -1, None),
        ("Main", 1, None),
    ],
)
def test_step_number_keeps_prefix_padding_and_suffix(
    number: str, by: int, expected: str | None
) -> None:
    assert numbering.step_number(number, by) == expected


@pytest.mark.parametrize(
    ("number", "taken", "expected"),
    [
        ("1m04", set(), "1m04a"),
        ("1m04", {"1m04a"}, "1m04b"),
        ("1m04a", set(), "1m04b"),
        ("1M04", {"1m04a"}, "1M04b"),
    ],
)
def test_split_number_takes_the_next_free_letter(
    number: str, taken: set[str], expected: str
) -> None:
    assert numbering.split_number(number, taken) == expected


def test_split_number_after_z_takes_the_next_plain_number() -> None:
    taken = {f"1m04{chr(c)}" for c in range(ord("a"), ord("z") + 1)}
    assert numbering.split_number("1m04", taken) == "1m05"
    assert numbering.split_number("1m04", taken | {"1m05"}) == "1m06"
    letters = {f"main{chr(c)}" for c in range(ord("a"), ord("z") + 1)}
    assert numbering.split_number("Main", letters) == "Main"


@pytest.mark.parametrize(
    ("number", "reel", "expected"),
    [
        ("1m05", "3", "3m05"),
        ("12M05a", "2", "2M05a"),
        ("1m05", "B", "1m05"),
        ("A12", "3", "A12"),
    ],
)
def test_with_reel(number: str, reel: str, expected: str) -> None:
    assert numbering.with_reel(number, reel) == expected


def test_suffixes() -> None:
    assert numbering.suffix_of("1m04a") == "a"
    assert numbering.suffix_of("1m04") == ""
    assert numbering.suffix_of("Main") == ""
    assert numbering.with_suffix("1m05", "s") == "1m05s"
    assert numbering.with_suffix("1m05a", "") == "1m05"
    assert numbering.with_suffix("Main", "s") == "Main"


def test_last_number_matches_long_input_quickly() -> None:
    # Matching stays linear: this would take minutes if it backtracked.
    assert numbering.suffix_of("0" * 50_000 + "!") == "!"
    assert numbering.step_number("x" * 50_000, 1) is None
    assert numbering.with_suffix("1m05", "s") == "1m05s"
