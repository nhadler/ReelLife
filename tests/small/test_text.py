"""Title Case for names and labels (cues.text)."""

import pytest

from cues import text
from cues.templatetags import text as text_tags


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("first cue # in the reel", "First Cue # in the Reel"),
        ("start at", "Start At"),  # the last word is capitalised
        ("a new IN for one cue", "A New IN for One Cue"),
        ("The ReelLife app", "The ReelLife App"),
        ("Sign-in is unavailable", "Sign-In Is Unavailable"),
        ("Rewrite/revisions", "Rewrite/Revisions"),
        ("Keep each cue's suffix", "Keep Each Cue's Suffix"),
        ("1m01 and 1m02", "1m01 and 1m02"),
        ("", ""),
    ],
)
def test_title_case(given: str, expected: str) -> None:
    assert text.title_case(given) == expected


def test_filter() -> None:
    assert text_tags.title_case("frame rate") == "Frame Rate"
