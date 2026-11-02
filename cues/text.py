"""Title Case for names and labels.

Nick, Oct 31: "if it's a title, both words are capitalised". Headings,
buttons, tabs, menu items, column headers and field labels use Title
Case; sentences (help text, messages) don't. Small joining words stay
lower case unless first or last, and words with capitals past their
first letter (IN, OUT, BPM, ReelLife) or with digits (#, 1m01, v2) are
left as they are. A word in brackets is capitalised inside them.
"""

import re

SMALL_WORDS = frozenset(
    "a an and as at but by for from in into nor of on or per the to vs via"
    " with".split()
)
_PARTS = re.compile(r"[^\s/–]+|[\s/–]+")
_LETTER = re.compile(r"[A-Za-z]")
_DIGIT = re.compile(r"[0-9]")


def title_case(text: str) -> str:
    """Returns `text` in Title Case.

    >>> title_case("first cue # in the reel")
    'First Cue # in the Reel'
    """
    parts = _PARTS.findall(text)
    words = [i for i, part in enumerate(parts) if _LETTER.search(part)]
    out = []
    for i, part in enumerate(parts):
        lead = len(part) - len(part.lstrip("(\"'“‘"))
        lower = part.lower()
        if (
            i not in words
            or any(c.isupper() for c in part[lead + 1 :])
            or _DIGIT.search(part)
        ):
            out.append(part)  # spaces, '#', '1m01', 'v2', IN, ReelLife
        elif lower.rstrip(".") in SMALL_WORDS and i not in (
            words[0],
            words[-1],
        ):
            out.append(lower)
        else:
            # Each part of a hyphenated word: Sign-In, E-Mail, (Advanced).
            out.append(
                part[:lead]
                + "-".join(
                    p[:1].upper() + p[1:] for p in part[lead:].split("-")
                )
            )
    return "".join(out)
