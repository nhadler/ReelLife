"""Cubase Pro / Nuendo track archive XML.

Cues are cycle markers on a marker track, exported with File > Export >
Selected Tracks. In the linear time base positions are seconds from the
project start, so the caller supplies the project start timecode; the
musical time base (ticks) can't be converted without the tempo map and
is refused.

The element names are a best guess, still to verify against a
real export: root `tracklist2`, marker track `MMarkerTrackEvent`, cycle
markers `MRangeMarkerEvent` with `Start`, `Length` and `Name`, the time
base in a `Domain` member whose `Type` is 1 for linear. Confirm against a
real export (fixtures/real/) before relying on this.

XML is parsed with defusedxml, so entity expansion and external entities
are refused.
"""

from collections.abc import Iterator
import decimal

# Only for types and ParseError; parsing goes through defusedxml.
import xml.etree.ElementTree as ET  # nosec B405

import defusedxml
from defusedxml import ElementTree as SafeET

from cues import timecode
from cues.importers import base

_LINEAR = "1"


def _named(obj: ET.Element, tag: str, name: str) -> ET.Element | None:
    for child in obj:
        if child.tag == tag and child.get("name") == name:
            return child
    return None


def _value(obj: ET.Element, tag: str, name: str) -> str | None:
    element = _named(obj, tag, name)
    return None if element is None else element.get("value")


def _objects(root: ET.Element, cls: str) -> Iterator[ET.Element]:
    for element in root.iter("obj"):
        if element.get("class") == cls:
            yield element


def _seconds_to_frames(
    seconds: str, rate: timecode.Rate, start_frames: int
) -> int:
    """Converts real seconds from project start to a frame count."""
    value = decimal.Decimal(seconds) * decimal.Decimal(rate.fps.numerator)
    value /= decimal.Decimal(rate.fps.denominator)
    rounded = int(value.to_integral_value(rounding=decimal.ROUND_HALF_UP))
    return start_frames + rounded


def _check_time_base(track: ET.Element) -> str | None:
    """Returns a warning, or raises if the track is in musical time."""
    domain = next(
        (
            element
            for element in track.iter("member")
            if element.get("name") == "Domain"
        ),
        None,
    )
    kind = None if domain is None else _value(domain, "int", "Type")
    if kind is None:
        return "The marker track doesn't state its time base; assumed linear."
    if kind != _LINEAR:
        raise base.ImportFormatError(
            "The marker track uses the musical time base, which can't be "
            "converted to timecode. In Cubase or Nuendo, switch the marker "
            "track to linear time and export it again."
        )
    return None


def parse(
    data: bytes, *, rate: timecode.Rate, start_frames: int
) -> base.ParsedFile:
    """Reads cycle markers from a track archive.

    Args:
        data: The XML file's bytes.
        rate: The project's frame rate.
        start_frames: The project start timecode in frames (what position
            0 in the DAW corresponds to).

    Returns:
        One row per cycle marker; position markers are ignored.

    Raises:
        base.ImportFormatError: Not well-formed XML, unsafe XML, not a
            track archive, or a marker track in musical time.
    """
    try:
        root = SafeET.fromstring(data)
    except defusedxml.DefusedXmlException as exc:
        raise base.ImportFormatError(
            "The XML file uses entities or external references, which "
            "aren't allowed."
        ) from exc
    except (ET.ParseError, LookupError) as exc:
        # LookupError: the XML declaration names an unknown encoding.
        raise base.ImportFormatError(f"Not a valid XML file: {exc}") from exc
    if root.tag != "tracklist2":
        raise base.ImportFormatError(
            "This isn't a Cubase or Nuendo track archive. Select the marker "
            "track and use File > Export > Selected Tracks."
        )
    warnings: list[str] = []
    rows: list[base.ImportRow] = []
    tracks = list(_objects(root, "MMarkerTrackEvent"))
    for track in tracks:
        warning = _check_time_base(track)
        if warning:
            warnings.append(warning)
        for marker in _objects(track, "MRangeMarkerEvent"):
            label = _value(marker, "string", "Name") or ""
            start = _value(marker, "float", "Start")
            length = _value(marker, "float", "Length")
            tc_in: int | None
            tc_out: int | None = None
            try:
                tc_in = _seconds_to_frames(start or "", rate, start_frames)
                if length is not None:
                    end = decimal.Decimal(start or "0") + decimal.Decimal(
                        length
                    )
                    tc_out = _seconds_to_frames(str(end), rate, start_frames)
            except decimal.InvalidOperation:
                warnings.append(f"Marker {label!r} has no readable position.")
                tc_in = tc_out = None
            number, title = base.split_name(label)
            rows.append(
                base.ImportRow(
                    label=label,
                    number=number,
                    title=title,
                    tc_in=tc_in,
                    tc_out=tc_out,
                    line=len(rows) + 1,
                )
            )
    if not tracks:
        warnings.append("No marker track found in the archive.")
    return base.ParsedFile(
        source="Cubase/Nuendo track archive", rows=rows, warnings=warnings
    )
