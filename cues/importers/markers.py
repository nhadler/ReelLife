"""Cues from markers that each have one position.

Pro Tools session text and MIDI files both give markers a start only.
A cue is an IN marker and its OUT ('1m01 Main Title IN', '1m01 MX OUT'),
or a bare cue number, which ends at the next marker of any kind.
Markers whose name has no digit (FFOA, EC, a scene name) start nothing
but can end a cue.
"""

from collections.abc import Iterable
import dataclasses

from cues.importers import base


@dataclasses.dataclass(frozen=True)
class Marker:
    """A marker: where it came from, its position and what its name says.

    Attributes:
        line: Its line (or marker number), for warnings and the preview.
        at: Position in frames.
        name: The name as written.
        role: 'in', 'out', or None for a bare name.
        number: The cue number in the name, if any.
        title: The rest of the name.
    """

    line: int
    at: int
    name: str
    role: str | None
    number: str
    title: str

    @classmethod
    def named(cls, line: int, at: int, name: str) -> "Marker":
        """Reads the role, number and title from the name."""
        role, rest = base.marker_role(name)
        number, title = base.split_name(rest)
        return cls(line, at, name, role, number, title)

    def starts_nothing(self) -> bool:
        """FFOA, EC and the like: no IN/OUT and no cue number."""
        return self.role is None and not is_cue_number(self.number)


def is_cue_number(number: str) -> bool:
    """'1m04' starts a cue; 'EC', 'FFOA' or a scene name doesn't."""
    return any(char.isdigit() for char in number)


def _end(
    marker: Marker,
    later: list[Marker],
    used: set[int],
    warnings: list[str],
    unit: str,
) -> int | None:
    """Where a cue ends: its own OUT, else the next marker of any kind."""
    key = marker.number.lower()
    for other in later:
        if (
            other.role == "out"
            and other.number.lower() == key
            and other.line not in used
        ):
            used.add(other.line)
            return other.at
    if later:
        warnings.append(
            f"{unit} {marker.line}: {marker.number} has no OUT; it ends at "
            f"the next marker ('{later[0].name}')."
        )
        return later[0].at
    warnings.append(
        f"{unit} {marker.line}: {marker.number} has no OUT and no marker "
        "after it; ignored."
    )
    return None


def cue_rows(
    found: Iterable[Marker], warnings: list[str], unit: str = "Line"
) -> list[base.ImportRow]:
    """Pairs markers into cues, in file order.

    Args:
        found: Every marker read, in any order.
        warnings: Gets a line per marker that was skipped or guessed at.
        unit: What `Marker.line` counts, for the warnings.

    Returns:
        A row per cue.
    """
    ordered = sorted(found, key=lambda m: (m.at, m.line))
    rows: list[base.ImportRow] = []
    used: set[int] = set()
    for index, marker in enumerate(ordered):
        if marker.role == "out":
            continue
        if marker.starts_nothing():
            warnings.append(
                f"{unit} {marker.line}: '{marker.name}' isn't a cue; skipped."
            )
            continue
        end = _end(marker, ordered[index + 1 :], used, warnings, unit)
        if end is not None:
            rows.append(
                base.ImportRow(
                    marker.name,
                    marker.number,
                    marker.title,
                    marker.at,
                    end,
                    line=marker.line,
                )
            )
    for marker in ordered:
        if (
            marker.role == "out"
            and marker.line not in used
            and is_cue_number(marker.number)
        ):
            warnings.append(
                f"{unit} {marker.line}: {marker.number} OUT has no IN; ignored."
            )
    return sorted(rows, key=lambda r: r.line)
