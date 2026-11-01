"""Templates contain nothing the strict CSP would block.

Scans every template in the repository rather than rendered pages, so a
template can't slip through just because no test renders it.
"""

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SKIP = {".venv", "node_modules", "fixtures", "reference"}

_SCRIPT_TAG = re.compile(r"<script\b([^>]*)>", re.IGNORECASE)
_EVENT_HANDLER = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
_STYLE_ATTR = re.compile(r"\sstyle\s*=", re.IGNORECASE)
_HX_ON = re.compile(r"\shx-on", re.IGNORECASE)


def _templates() -> list[pathlib.Path]:
    return sorted(
        path
        for path in _ROOT.rglob("*.html")
        if not _SKIP.intersection(path.relative_to(_ROOT).parts)
    )


def test_templates_found() -> None:
    assert _templates(), "no templates scanned"


@pytest.mark.parametrize(
    "path", _templates(), ids=lambda p: str(p.relative_to(_ROOT))
)
def test_template_is_csp_safe(path: pathlib.Path) -> None:
    text = path.read_text(encoding="utf-8")
    for attrs in _SCRIPT_TAG.findall(text):
        assert "src=" in attrs or 'type="application/json"' in attrs, (
            f"inline <script> in {path.name}"
        )
    assert not _EVENT_HANDLER.search(text), f"on*= handler in {path.name}"
    assert not _STYLE_ATTR.search(text), f"style= attribute in {path.name}"
    assert not _HX_ON.search(text), f"hx-on attribute in {path.name}"


_SHORT_COMMENT = re.compile(r"\{#(.*?)#\}", re.DOTALL)


@pytest.mark.parametrize(
    "path", _templates(), ids=lambda p: str(p.relative_to(_ROOT))
)
def test_short_comments_stay_on_one_line(path: pathlib.Path) -> None:
    """Django prints a {# #} comment that spans lines as page text."""
    text = path.read_text(encoding="utf-8")
    for comment in _SHORT_COMMENT.findall(text):
        assert "\n" not in comment, f"multi-line {{# #}} in {path.name}"
