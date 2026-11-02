"""Title Case for names and labels.

Headings, buttons, tabs, menu items, column headers and field labels use Title
Case; sentences (help text, messages) don't. Small joining words stay
lower case unless first or last, and words with capitals past their
first letter (IN, OUT, BPM, ReelLife) or no letters (#, 1m01) are left
as they are.
"""

import re

SMALL_WORDS = frozenset(
    "a an and as at but by for from in into nor of on or per the to vs via"
    " with".split()
)
_PARTS = re.compile(r"[^\s/–]+|[\s/–]+")
_LETTER = re.compile(r"[A-Za-z]")


def title_case(text: str) -> str:
    """Returns `text` in Title Case.

    >>> title_case("first cue # in the reel")
    'First Cue # in the Reel'
    """
    parts = _PARTS.findall(text)
    words = [i for i, part in enumerate(parts) if _LETTER.search(part)]
    out = []
    for i, part in enumerate(parts):
        lower = part.lower()
        if i not in words or any(c.isupper() for c in part[1:]):
            out.append(part)  # spaces, '#', '1m01', IN, ReelLife
        elif lower in SMALL_WORDS and i not in (words[0], words[-1]):
            out.append(lower)
        else:
            # Each part of a hyphenated word: Sign-In, E-Mail.
            out.append("-".join(p[:1].upper() + p[1:] for p in part.split("-")))
    return "".join(out)
