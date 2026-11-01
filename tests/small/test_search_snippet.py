"""The snippet Ctrl+K shows for a cue found by its notes."""

from cues.services import search


def test_snippets_cut_between_words() -> None:
    text = (
        "The orchestra holds a long low note while the camera pans "
        "slowly across the harbour and the door finally closes on "
        "the keeper, then silence until the storm arrives at dawn."
    )
    assert search.snippet(text, "DOOR") == (
        "…the harbour and the door finally closes on the keeper, then…"
    )


def test_short_notes_come_whole_on_one_line() -> None:
    assert search.snippet("Door at\nthe  start", "door") == "Door at the start"


def test_a_word_not_found_shows_the_start() -> None:
    assert search.snippet("no match here", "zzz") == "no match here"


def test_one_long_word_is_cut_mid_word() -> None:
    one_word = "x" * 30 + "door" + "y" * 50
    assert search.snippet(one_word, "door") == (
        "…" + "x" * 24 + "door" + "y" * 40 + "…"
    )
