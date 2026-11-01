"""Cubase/Nuendo track archive importer."""

import hypothesis
from hypothesis import strategies as st
import pytest

from cues import timecode
from cues.importers import base
from cues.importers import cubase
from tests import golden

FIXTURE = golden.FIXTURES / "cubase_marker_track_linear.xml"


def _archive(
    events: str, domain: str = '<int name="Type" value="1"/>'
) -> bytes:
    return f"""<?xml version="1.0"?>
<tracklist2><list name="track" type="obj">
  <obj class="MMarkerTrackEvent" ID="1">
    <obj class="MListNode" name="Node" ID="2">
      <member name="Domain">{domain}</member>
      <list name="Events" type="obj">{events}</list>
    </obj>
  </obj>
</list></tracklist2>""".encode()


def _cycle(name: str, start: str, length: str | None = "10") -> str:
    length_xml = (
        "" if length is None else f'<float name="Length" value="{length}"/>'
    )
    return (
        '<obj class="MRangeMarkerEvent" ID="9">'
        f'<float name="Start" value="{start}"/>{length_xml}'
        f'<string name="Name" value="{name}"/></obj>'
    )


def test_golden() -> None:
    # The fixture's structure is a guess (see its header comment); this
    # pins how we read it at 24 fps from project start 0:00:00:00.
    parsed = cubase.parse(
        FIXTURE.read_bytes(), rate=timecode.RATE_24, start_frames=0
    )
    golden.check(FIXTURE, "24fps", golden.describe(parsed, timecode.RATE_24))


def test_cycle_markers_become_cues_and_position_markers_dont() -> None:
    parsed = cubase.parse(
        FIXTURE.read_bytes(), rate=timecode.RATE_24, start_frames=0
    )
    assert [(r.number, r.title, r.tc_in, r.tc_out) for r in parsed.rows] == [
        ("1m01", "Main Title", 86_640, 88_572),
        ("1m02", "The Chase", 90_720, 93_896),
    ]
    assert parsed.frame_rate is None  # the archive doesn't state one


def test_seconds_are_real_time_at_23_976() -> None:
    # 60 real seconds at 23.976 are 1,438.56 frames -> 1,439; 61 s are
    # 1,462.54 -> 1,463.
    parsed = cubase.parse(
        _archive(_cycle("1m01 A", "60", "1")),
        rate=timecode.RATE_23_976,
        start_frames=86_400,
    )
    assert (parsed.rows[0].tc_in, parsed.rows[0].tc_out) == (
        86_400 + 1_439,
        86_400 + 1_463,
    )


def test_marker_without_length_has_no_out() -> None:
    parsed = cubase.parse(
        _archive(_cycle("1m01", "1", None)),
        rate=timecode.RATE_24,
        start_frames=0,
    )
    assert (parsed.rows[0].tc_in, parsed.rows[0].tc_out) == (24, None)


def test_unreadable_position_is_a_warning() -> None:
    parsed = cubase.parse(
        _archive(_cycle("1m01", "soon")), rate=timecode.RATE_24, start_frames=0
    )
    assert parsed.rows[0].tc_in is None
    assert parsed.warnings == ["Marker '1m01' has no readable position."]


def test_musical_time_base_is_refused() -> None:
    with pytest.raises(base.ImportFormatError, match="linear time"):
        cubase.parse(
            _archive(_cycle("1m01", "1"), '<int name="Type" value="0"/>'),
            rate=timecode.RATE_24,
            start_frames=0,
        )


def test_missing_time_base_is_assumed_linear() -> None:
    parsed = cubase.parse(
        _archive(_cycle("1m01", "1"), ""), rate=timecode.RATE_24, start_frames=0
    )
    assert parsed.rows[0].tc_in == 24
    assert parsed.warnings == [
        "The marker track doesn't state its time base; assumed linear."
    ]


def test_archive_without_marker_track() -> None:
    parsed = cubase.parse(
        b"<tracklist2/>", rate=timecode.RATE_24, start_frames=0
    )
    assert not parsed.rows
    assert parsed.warnings == ["No marker track found in the archive."]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"<project/>", "track archive"),
        (b"<tracklist2>", "valid XML"),
        (b"", "valid XML"),
        (b'<?xml version="1.0" encoding="bogus"?><tracklist2/>', "valid XML"),
    ],
)
def test_wrong_files_are_refused(data: bytes, message: str) -> None:
    with pytest.raises(base.ImportFormatError, match=message):
        cubase.parse(data, rate=timecode.RATE_24, start_frames=0)


BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">
<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;">]>
<tracklist2>&lol3;</tracklist2>"""

XXE = b"""<?xml version="1.0"?>
<!DOCTYPE t [<!ENTITY secret SYSTEM "file:///etc/passwd">]>
<tracklist2>&secret;</tracklist2>"""


@pytest.mark.parametrize("data", [BILLION_LAUGHS, XXE], ids=["bomb", "xxe"])
def test_entity_attacks_are_refused(data: bytes) -> None:
    with pytest.raises(base.ImportFormatError, match="entities"):
        cubase.parse(data, rate=timecode.RATE_24, start_frames=0)


@hypothesis.settings(max_examples=150, deadline=None)
@hypothesis.given(cut=st.integers(0, 2_000), junk=st.binary(max_size=40))
def test_damaged_files_never_crash(cut: int, junk: bytes) -> None:
    data = FIXTURE.read_bytes()
    damaged = data[: cut % len(data)] + junk + data[cut % len(data) :]
    try:
        cubase.parse(damaged, rate=timecode.RATE_24, start_frames=0)
    except base.ImportFormatError:
        pass
