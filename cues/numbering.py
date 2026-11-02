"""Cue number helpers shared by imports, the new cue dialog and tools."""

import re

_LAST_NUMBER = re.compile(r"^(?P<prefix>.*?)(?P<digits>\d+)(?P<suffix>\D*)$")


def next_number(number: str) -> str:
    """Returns the cue number after `number`.

    The last group of digits goes up by one, keeping its zero padding; a
    trailing letter suffix is dropped ('1m04a' -> '1m05').

    Raises:
        ValueError: If the number has no digits to increment.
    """
    match = _LAST_NUMBER.match(number)
    if match is None:
        raise ValueError(f"no digits to increment in {number!r}")
    digits = match["digits"]
    incremented = str(int(digits) + 1).zfill(len(digits))
    return f"{match['prefix']}{incremented}"


def is_valid(number: str, pattern: str) -> bool:
    """Whether a cue number matches the project's cue number pattern."""
    try:
        return re.search(pattern, number) is not None
    except re.error:
        return False


def misfit(number: str, pattern: str) -> str:
    """Why a new or renamed cue can't take `number`, or '' if it can.

    Typed numbers must match Valid Cue Numbers, as imported numbers do.
    """
    if is_valid(number, pattern):
        return ""
    return (
        f"{number} doesn't fit this project's Valid Cue Numbers "
        "(Settings, Cue Sheet)."
    )


def _natural_key(number: str) -> tuple[tuple[int, str], ...]:
    """Sorts '1m2' before '1m10' and '2m01' after both."""
    return tuple(
        (int(part), "") if part.isdigit() else (-1, part.lower())
        for part in re.findall(r"\d+|\D+", number)
    )


def suggest_number(
    in_picture: list[str], picture_name: str, taken: set[str]
) -> str:
    """Suggests a number for a new cue in a picture.

    After the picture's highest cue number ('1m02' -> '1m03'); for an
    empty reel named with digits, its first cue ('2' -> '2m01'). Numbers
    already used in the project (compared case-insensitively) are skipped.

    Args:
        in_picture: Cue numbers already in the picture.
        picture_name: The picture's name, e.g. '2'.
        taken: Every active cue number in the project, lowercased.

    Returns:
        A free cue number.
    """
    if in_picture:
        candidate = next_number(max(in_picture, key=_natural_key))
    elif picture_name.isdigit():
        candidate = f"{picture_name}m01"
    else:
        candidate = "1m01"
    while candidate.lower() in taken:
        candidate = next_number(candidate)
    return candidate


_VERSION = re.compile(r"^v?(?P<major>\d+)(?:\.(?P<minor>\d+))?$", re.IGNORECASE)


def next_version_label(labels: list[str], *, minor: bool) -> str:
    """The label for a cue's next version.

    Args:
        labels: The cue's existing version labels ('v1', 'v1.1', 'v2').
        minor: Increment the minor number (v1 -> v1.1) rather than the
            major (v1.1 -> v2); the version increment setting.

    Returns:
        'v1' for a cue's first version, else the next label. Labels that
        don't look like versions are ignored.
    """
    parsed = sorted(
        (int(m["major"]), int(m["minor"] or 0))
        for label in labels
        if (m := _VERSION.match(label.strip()))
    )
    if not parsed:
        return "v1"
    major, minor_number = parsed[-1]
    if minor:
        return f"v{major}.{minor_number + 1}"
    return f"v{major + 1}"


def step_number(number: str, by: int) -> str | None:
    """Moves a cue number's last digits by `by`, keeping everything else.

    Zero padding, the prefix and a letter suffix all stay ('1m05s', +2 ->
    '1m07s'), unlike next_number. Used by the batch and renumber tools.

    Returns:
        The new number, or None when it has no digits or would go below 0.
    """
    match = _LAST_NUMBER.match(number)
    if match is None:
        return None
    digits = match["digits"]
    value = int(digits) + by
    if value < 0:
        return None
    return f"{match['prefix']}{str(value).zfill(len(digits))}{match['suffix']}"


def split_number(number: str, taken: set[str]) -> str:
    """The number for the new half of a split cue.

    The next free letter after the number ('1m04' -> '1m04a', or
    '1m04b' when that is taken; '1m04a' -> '1m04b'). After 'z' it falls
    back to the next free plain number.

    Args:
        number: The cue being split.
        taken: Every active cue number in the project, lowercased.
    """
    match = re.match(r"^(?P<base>.*?\d)(?P<letter>[a-z]?)$", number, re.I)
    base, start = (match["base"], match["letter"]) if match else (number, "")
    first = ord(start.lower()) + 1 if start else ord("a")
    for code in range(first, ord("z") + 1):
        candidate = f"{base}{chr(code)}"
        if candidate.lower() not in taken:
            return candidate
    candidate = next_number(number) if _LAST_NUMBER.match(number) else number
    while candidate.lower() in taken:
        candidate = next_number(candidate)
    return candidate


_REEL_NUMBER = re.compile(r"^(?P<reel>\d+)(?P<rest>[mM].*)$")


def with_reel(number: str, reel: str) -> str:
    """Replaces the reel part of a number like '1m05' ('3' -> '3m05').

    Numbers without a reel part, or a reel that isn't digits, stay as
    they are.
    """
    match = _REEL_NUMBER.match(number)
    if match is None or not reel.isdigit():
        return number
    return f"{reel}{match['rest']}"


def suffix_of(number: str) -> str:
    """The letters after a number's last digits ('1m04a' -> 'a')."""
    match = _LAST_NUMBER.match(number)
    return match["suffix"] if match else ""


def with_suffix(number: str, suffix: str) -> str:
    """Replaces the letters after the last digits ('1m05', 's' -> '1m05s')."""
    match = _LAST_NUMBER.match(number)
    if match is None:
        return number
    return f"{match['prefix']}{match['digits']}{suffix}"
