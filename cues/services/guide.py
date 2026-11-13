"""The user guide (cues/guide/*.md): Help pages and the MCP server's how-to.

One Markdown file per topic, written for the people using ReelTime; the
web app renders them (Help), and the MCP server hands the same text to
an assistant asked how to do something.
"""

import dataclasses
import functools
import pathlib

import markdown

GUIDE = pathlib.Path(__file__).resolve().parent.parent / "guide"

# Slugs in reading order.
ORDER = (
    "getting-started",
    "picture-and-turnovers",
    "cues-and-versions",
    "importing",
    "conform",
    "tools",
    "credits-and-cue-sheets",
    "exports",
    "progress-and-deadlines",
    "schedule",
    "undo-and-history",
    "settings-and-roles",
    "ai-assistant",
)


@dataclasses.dataclass(frozen=True)
class Topic:
    """One page of the guide."""

    slug: str
    title: str
    text: str  # Markdown, starting with its "# Title"


@functools.cache
def topics() -> tuple[Topic, ...]:
    """Every topic, in reading order (read once per process)."""
    found = []
    for slug in ORDER:
        text = (GUIDE / f"{slug}.md").read_text(encoding="utf-8")
        title = text.splitlines()[0].removeprefix("# ").strip()
        found.append(Topic(slug, title, text))
    return tuple(found)


def topic(slug: str) -> Topic | None:
    """The topic with this slug, or None."""
    return next((t for t in topics() if t.slug == slug), None)


def html(found: Topic) -> str:
    """A topic as HTML, without its title (the page shows that).

    The guide is our own files, not user input, so its HTML is trusted.
    """
    body = found.text.split("\n", 1)[1] if "\n" in found.text else ""
    return markdown.markdown(body, extensions=["sane_lists"])
