"""Pro Tools session text importer."""

import pathlib

import hypothesis
from hypothesis import strategies as st
import pytest

from cues import timecode
from cues.importers import base
from cues.importers import protools
from tests import golden

FIXTURES = golden.FIXTURES
PTULSCONV = FIXTURES / "third_party" / "ptulsconv"
CLIPS = protools.Method.CLIPS
MARKERS = protools.Method.MARKERS

# (fixture, golden variant, method, track prefixes)
CASES = [
    (FIXTURES / "protools_clips_23976.txt", "clips", CLIPS, ("Cues",)),
    (FIXTURES / "protools_markers_2997df.txt", "markers", MARKERS, ()),
    # Real exports (ADR sessions): parse every track to test the parser.
    (PTULSCONV / "Robin Hood Spotting.txt", "clips-all", CLIPS, ("",)),
    (PTULSCONV / "Robin Hood SpottingDF.txt", "clips-all", CLIPS, ("",)),
    (PTULSCONV / "Test for ptulsconv.txt", "clips-all", CLIPS, ("",)),
    (PTULSCONV / "Test for ptulsconv.txt", "markers", MARKERS, ()),
]
# The composer's own exports, when added: every method, every track.
REAL = sorted((FIXTURES / "real").glob("*.txt"))


def _read(path: pathlib.Path) -> str:
    return protools.decode(path.read_bytes())


def _parse(
    path: pathlib.Path, method: protools.Method, prefixes: tuple[str, ...]
) -> base.ParsedFile:
    return protools.parse(
        _read(path), method=method, track_prefixes=prefixes or ("Cues",)
    )


def _rate(parsed: base.ParsedFile) -> timecode.Rate:
    assert parsed.frame_rate is not None
    return timecode.rate_for(
        parsed.frame_rate, drop_frame=bool(parsed.drop_frame)
    )


@pytest.mark.parametrize(
    ("path", "variant", "method", "prefixes"),
    CASES,
    ids=[f"{c[0].name}-{c[1]}" for c in CASES],
)
def test_golden(
    path: pathlib.Path,
    variant: str,
    method: protools.Method,
    prefixes: tuple[str, ...],
) -> None:
    parsed = _parse(path, method, prefixes)
    golden.check(path, variant, golden.describe(parsed, _rate(parsed)))


@pytest.mark.parametrize("path", REAL, ids=lambda p: p.name)
@pytest.mark.parametrize("method", list(protools.Method))
def test_real_exports(path: pathlib.Path, method: protools.Method) -> None:
    try:
        parsed = _parse(path, method, ("",))
    except base.ImportFormatError as exc:
        pytest.skip(str(exc))
    golden.check(path, f"{method}-all", golden.describe(parsed, _rate(parsed)))


def test_clip_method_matches_the_fixture_readme() -> None:
    # fixtures/README.md: expected IN/OUT frames at 23.976.
    parsed = _parse(FIXTURES / "protools_clips_23976.txt", CLIPS, ("Cues",))
    assert [(r.number, r.tc_in, r.tc_out) for r in parsed.rows] == [
        ("1m01", 86_640, 88_572),
        ("1m02", 90_720, 93_896),
        ("1m03", 95_079, 97_536),  # two clips; merge.plan joins them
        ("1m03", 97_680, 98_400),
    ]
    assert (parsed.frame_rate, parsed.drop_frame) == ("23.976", False)


def test_dialogue_track_is_ignored() -> None:
    parsed = _parse(FIXTURES / "protools_clips_23976.txt", CLIPS, ("Cues",))
    assert "DX_01" not in [r.label for r in parsed.rows]


def test_prefixes_are_case_insensitive() -> None:
    parsed = _parse(
        FIXTURES / "protools_clips_23976.txt", CLIPS, ("cues", "MX")
    )
    assert len(parsed.rows) == 4


def test_markers_pair_in_and_out() -> None:
    parsed = _parse(FIXTURES / "protools_markers_2997df.txt", MARKERS, ())
    assert [(r.number, r.title) for r in parsed.rows] == [
        ("101m01", "Cold Open"),
        ("101m02", "Title Card"),
        ("101m03s", "Radio Song"),
    ]
    assert (parsed.frame_rate, parsed.drop_frame) == ("29.97", True)
    assert parsed.rows[0].tc_in == timecode.parse(
        "01:00:15;10", timecode.RATE_29_97_DF
    )


def test_stereo_channel_two_is_skipped() -> None:
    parsed = _parse(PTULSCONV / "Robin Hood SpottingDF.txt", CLIPS, ("",))
    labels = [r.label for r in parsed.rows]
    assert "RobinHood.1-01.L" in labels
    assert "RobinHood.1-01.R" not in labels


@pytest.mark.parametrize("method", list(protools.Method))
def test_min_sec_export_is_refused(method: protools.Method) -> None:
    with pytest.raises(base.ImportFormatError, match="Time Format"):
        _parse(PTULSCONV / "Robin Hood SpottingMS.txt", method, ("Cues",))


HEADER = (
    "SESSION NAME:\tTest\nSESSION START TIMECODE:\t01:00:00:00\n"
    "TIMECODE FORMAT:\t{fmt}\n\n\n"
)
TRACKS = (
    "T R A C K  L I S T I N G\nTRACK NAME:\tCues\nCOMMENTS:\t\n"
    "CHANNEL \tEVENT   \tCLIP NAME   \tSTART TIME    \tEND TIME      \n"
    "{rows}\n\n"
)
MARKERS_TABLE = (
    "M A R K E R S  L I S T I N G\n"
    "#   \tLOCATION     \tTIME REFERENCE    \tUNITS    \tNAME     \tCOMMENTS\n"
    "{rows}\n"
)


def _session(
    fmt: str = "24 Frame", tracks: str | None = None, markers: str | None = None
) -> str:
    text = HEADER.format(fmt=fmt)
    if tracks is not None:
        text += TRACKS.format(rows=tracks)
    if markers is not None:
        text += MARKERS_TABLE.format(rows=markers)
    return text


def _marker(n: int, location: str, name: str) -> str:
    return f"{n}\t{location}\t0\tSamples\t{name}\t"


def test_not_a_session_export() -> None:
    with pytest.raises(base.ImportFormatError, match="Session Info as Text"):
        protools.parse("hello\nworld\n", method=CLIPS)


def test_thirty_drop_frame_is_refused() -> None:
    with pytest.raises(base.ImportFormatError, match="drop-frame"):
        protools.parse(_session("30 Drop Frame", tracks=""), method=CLIPS)


@pytest.mark.parametrize(
    ("method", "message"),
    [(CLIPS, "Include Track EDLs"), (MARKERS, "Include Markers")],
)
def test_missing_section(method: protools.Method, message: str) -> None:
    with pytest.raises(base.ImportFormatError, match=message):
        protools.parse(_session(), method=method)


def test_bad_timecode_becomes_a_warning() -> None:
    rows = "1\t1\t1m01 A\t01:00:00:30\t01:00:10:00\n1\t2\t1m02 B\t\t01:00:20:00"
    parsed = protools.parse(_session(tracks=rows), method=CLIPS)
    assert [(r.number, r.tc_in) for r in parsed.rows] == [
        ("1m01", None),
        ("1m02", None),
    ]
    assert parsed.warnings == [
        "Line 10: start frames must be < 24 at 24: '01:00:00:30'"
    ]


def test_columns_fall_back_to_positions() -> None:
    text = _session(tracks="1\t1\t1m01 A\t01:00:00:00\t01:00:10:00").replace(
        "CHANNEL \tEVENT   \tCLIP NAME   \tSTART TIME    \tEND TIME      ",
        "CHANNEL \tEREIGNIS\tCLIPNAME\tSTARTZEIT\tENDZEIT",
    )
    parsed = protools.parse(text, method=CLIPS)
    assert (parsed.rows[0].tc_in, parsed.rows[0].tc_out) == (86_400, 86_640)


def test_rows_before_a_header_are_ignored() -> None:
    text = _session(tracks="").replace(
        "TRACK NAME:\tCues\n", "TRACK NAME:\tCues\n1\t1\t1m01\t01:00:00:00\n"
    )
    assert not protools.parse(text, method=CLIPS).rows


def test_marker_pairing_warnings() -> None:
    markers = "\n".join(
        [
            _marker(1, "01:00:00:00", "1m01 A IN"),
            _marker(2, "01:00:01:00", "1m01 Again IN"),
            _marker(3, "01:00:02:00", "1m01 OUT"),
            _marker(4, "01:00:03:00", "1m02 OUT"),
            _marker(5, "01:00:04:00", "1m03 C IN"),
            _marker(6, "01:00:05:00", "Scene 4"),
        ]
    )
    parsed = protools.parse(_session(markers=markers), method=MARKERS)
    # The first IN takes 1m01's OUT; a cue with no OUT of its own ends at
    # the next marker; the merge step joins the two 1m01s.
    assert [(r.number, r.title, r.tc_in, r.tc_out) for r in parsed.rows] == [
        ("1m01", "A", 86_400, 86_448),
        ("1m01", "Again", 86_424, 86_448),
        ("1m03", "C", 86_496, 86_520),
    ]
    assert parsed.warnings == [
        "Line 9: 1m01 has no OUT; it ends at the next marker ('1m01 OUT').",
        "Line 12: 1m03 has no OUT; it ends at the next marker ('Scene 4').",
        "Line 13: 'Scene 4' isn't a cue; skipped.",
        "Line 11: 1m02 OUT has no IN; ignored.",
    ]


def test_the_composers_markers() -> None:
    # As in the composer's session: MX IN / MX OUT, bare cue numbers, and
    # markers that aren't cues.
    markers = "\n".join(
        [
            _marker(1, "01:00:00:00", "1m01 MX IN"),
            _marker(2, "01:00:10:00", "1m02"),
            _marker(3, "01:00:20:00", "1m02 MX OUT"),
            _marker(4, "01:00:30:00", "EC"),
            _marker(5, "01:00:40:00", "OUT"),
        ]
    )
    parsed = protools.parse(_session(markers=markers), method=MARKERS)
    assert [(r.number, r.title, r.tc_in, r.tc_out) for r in parsed.rows] == [
        ("1m01", "", 86_400, 86_640),
        ("1m02", "", 86_640, 86_880),
    ]


def test_marker_edge_cases() -> None:
    markers = "\n".join(
        [
            _marker(1, "01:00:00:99", "1m01 IN"),  # frames past 23
            _marker(2, "01:00:05:00", "1m02 IN"),
            _marker(3, "01:00:06:00", "Act OUT"),  # no cue number: silent
        ]
    )
    parsed = protools.parse(_session(markers=markers), method=MARKERS)
    assert [(r.number, r.tc_in, r.tc_out) for r in parsed.rows] == [
        ("1m02", 86_520, 86_544)
    ]
    assert len(parsed.warnings) == 2
    assert parsed.warnings[0].startswith("Line 8: location frames must be")
    assert "ends at the next marker ('Act OUT')" in parsed.warnings[1]


def test_mac_roman_exports_decode() -> None:
    assert protools.decode("Café".encode("mac_roman")) == "Café"
    assert protools.decode("﻿SESSION".encode()) == "SESSION"


_FUZZ_SOURCES = [_read(path) for path, *_ in CASES]


@hypothesis.settings(max_examples=150, deadline=None)
@hypothesis.given(
    source=st.sampled_from(_FUZZ_SOURCES),
    cuts=st.lists(st.integers(0, 10_000), max_size=6),
    junk=st.text(max_size=40),
    method=st.sampled_from(list(protools.Method)),
)
def test_damaged_files_never_crash(
    source: str, cuts: list[int], junk: str, method: protools.Method
) -> None:
    lines = source.splitlines()
    for cut in cuts:
        if lines:
            index = cut % len(lines)
            lines[index] = lines[index][: cut % 17] + junk
    try:
        protools.parse("\n".join(lines), method=method, track_prefixes=("",))
    except base.ImportFormatError:
        pass  # a clear refusal is fine; anything else is a bug


def test_marker_lines_outside_the_table_are_ignored() -> None:
    markers = "\n" + _marker(1, "01:00:00:00", "1m01 IN") + "\n\n"
    text = _session(markers=markers).replace(
        "M A R K E R S  L I S T I N G\n",
        "M A R K E R S  L I S T I N G\n" + _marker(9, "junk", "1m09 IN") + "\n",
    )
    parsed = protools.parse(text, method=MARKERS)
    assert not parsed.rows
    assert parsed.warnings == [
        "Line 10: 1m01 has no OUT and no marker after it; ignored."
    ]
