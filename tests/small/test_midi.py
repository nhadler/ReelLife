"""MIDI file marker importer."""

import io

import mido
import pytest

from cues import timecode
from cues.importers import base
from cues.importers import midi
from tests import golden

FIXTURE = golden.FIXTURES / "midi_markers_25.mid"
RATE = timecode.RATE_25
HOUR = 3600 * 25


def _file(*messages: mido.MetaMessage, ppq: int = 480) -> bytes:
    song = mido.MidiFile(type=0, ticks_per_beat=ppq)
    song.tracks.append(mido.MidiTrack(messages))
    out = io.BytesIO()
    song.save(file=out)
    return out.getvalue()


def test_golden() -> None:
    parsed = midi.parse(FIXTURE.read_bytes(), rate=RATE, start_frames=0)
    golden.check(FIXTURE, "25fps", golden.describe(parsed, RATE))


def test_tempo_map_and_smpte_offset() -> None:
    # 120 bpm, then 60 bpm from 20 s; the file starts at 1:00:00:00.
    parsed = midi.parse(FIXTURE.read_bytes(), rate=RATE, start_frames=0)
    assert [(r.number, r.tc_in, r.tc_out) for r in parsed.rows] == [
        ("1m01", HOUR + 10 * 25, HOUR + 24 * 25),
        ("1m02", HOUR + 30 * 25, HOUR + 40 * 25),
        ("1m03", HOUR + 40 * 25, HOUR + 50 * 25),
    ]
    assert "Marker 1: 'FFOA' isn't a cue; skipped." in parsed.warnings
    assert "Marker 7: 2m01 OUT has no IN; ignored." in parsed.warnings


def test_without_an_offset_the_given_start_is_used() -> None:
    data = _file(
        mido.MetaMessage("marker", text="1m01 IN", time=480),
        mido.MetaMessage("marker", text="1m01 OUT", time=480),
    )
    parsed = midi.parse(data, rate=RATE, start_frames=HOUR)
    row = parsed.rows[0]
    assert (row.tc_in, row.tc_out) == (HOUR + 12 + 1, HOUR + 25)


def test_real_time_at_23_976() -> None:
    data = _file(
        mido.MetaMessage("marker", text="1m01 IN", time=0),
        mido.MetaMessage("marker", text="1m01 OUT", time=480 * 2002),
    )
    rate = timecode.RATE_23_976
    row = midi.parse(data, rate=rate, start_frames=0).rows[0]
    assert row.tc_out == 24_000  # 1001 s of real time


def test_a_bad_offset_is_warned_about() -> None:
    data = _file(
        mido.MetaMessage("smpte_offset", frame_rate=30, frames=29),
        mido.MetaMessage("marker", text="1m01", time=480),
        mido.MetaMessage("marker", text="End", time=480),
    )
    parsed = midi.parse(data, rate=RATE, start_frames=HOUR)
    assert parsed.rows[0].tc_in == HOUR + 13
    assert "SMPTE offset is unusable" in parsed.warnings[0]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"not midi", "Not a readable MIDI file"),
        (_file(mido.MetaMessage("text", text="hi")), "no markers"),
    ],
)
def test_refused(data: bytes, message: str) -> None:
    with pytest.raises(base.ImportFormatError, match=message):
        midi.parse(data, rate=RATE, start_frames=0)


def test_smpte_division_is_refused() -> None:
    data = bytearray(_file(mido.MetaMessage("marker", text="1m01")))
    data[12:14] = (0xE7, 0x28)  # -25 fps, 40 ticks a frame
    with pytest.raises(base.ImportFormatError, match="SMPTE frames"):
        midi.parse(bytes(data), rate=RATE, start_frames=0)


def _smf(*tracks: bytes, ppq: int = 480, header: bytes = b"") -> bytes:
    """A type 1 file with these raw track bodies."""
    head = (6 + len(header)).to_bytes(4) + b"\x00\x01"
    head += len(tracks).to_bytes(2) + ppq.to_bytes(2) + header
    body = b"".join(b"MTrk" + len(t).to_bytes(4) + t for t in tracks)
    return b"MThd" + head + body


def _marker(text: bytes, delta: bytes = b"\x00") -> bytes:
    return delta + b"\xff\x06" + bytes([len(text)]) + text


END = b"\x00\xff\x2f\x00"


def test_long_runs_of_continuation_bytes_are_refused_quickly() -> None:
    # mido reads this delta time a byte at a time, in quadratic time.
    data = _smf(b"\xff" * 1_000_000 + b"\x00")
    with pytest.raises(base.ImportFormatError, match="longer than four"):
        midi.parse(data, rate=RATE, start_frames=0)


def test_channel_messages_running_status_and_sysex_are_walked() -> None:
    notes = b"\x00\x90\x40\x40" + b"\x00\x40\x00"  # note on, running status
    sysex = b"\x00\xf0\x03\x7e\x01\xf7"
    cue = _marker(b"1m01 IN", b"\x83\x60") + _marker(b"1m01 OUT", b"\x83\x60")
    tempo = b"\x00\xff\x51\x03\x0f\x42\x40" + END  # 60 bpm, in another track
    data = _smf(notes + sysex + cue + END, tempo)
    row = midi.parse(data, rate=RATE, start_frames=0).rows[0]
    # 480 ticks at 60 bpm: one second.
    assert (row.tc_in, row.tc_out) == (25, 50)


def test_markers_from_every_track_are_ordered_by_time() -> None:
    out = _marker(b"1m01 OUT", b"\x87\x40") + END  # tick 960
    in_ = _marker(b"1m01 IN", b"\x83\x60") + END  # tick 480
    row = midi.parse(_smf(out, in_), rate=RATE, start_frames=0).rows[0]
    assert (row.tc_in, row.tc_out) == (13, 25)


def test_an_offset_without_frames_is_warned_about() -> None:
    offset = b"\x00\xff\x54\x02\x01\x00"
    cue = _marker(b"1m01 IN") + _marker(b"1m01 OUT", b"\x83\x60")
    parsed = midi.parse(_smf(offset + cue + END), rate=RATE, start_frames=HOUR)
    assert parsed.rows[0].tc_in == HOUR
    assert "SMPTE offset is unusable: it is cut short" in parsed.warnings[0]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"MThd\x00\x00\x00\x06\x00", "MThd not found"),
        (b"MThd\x00\x00\x00\x02" + bytes(8), "header is cut short"),
        (b"MThd\x00\x00\x00\x30" + bytes(8), "header is cut short"),
        (_smf(header=b"\x00\x00"), "no markers"),
        (_smf(END)[:-6], "track is missing"),
        (_smf(END)[:-1], "longer than the file"),
        (_smf(b"\x00\xff\x06\x05ab"), "past the end of its track"),
        (_smf(b"\x00"), "past the end of its track"),
        (_smf(b"\x00\x40\x40" + END), "running status without"),
        (_smf(b"\x00\x90\x40\x80" + END), "data byte is out of range"),
        (_smf(b"\x00\xf4" + END), "undefined status byte 0xf4"),
        (_smf(b"\x00\xff\x51\x02\x07\xa1" + END), "tempo change is cut"),
    ],
)
def test_damaged_files_are_refused(data: bytes, message: str) -> None:
    with pytest.raises(base.ImportFormatError, match=message):
        midi.parse(data, rate=RATE, start_frames=0)


def test_zero_ticks_per_beat_is_refused() -> None:
    with pytest.raises(base.ImportFormatError, match="SMPTE frames"):
        midi.parse(
            _smf(_marker(b"1m01") + END, ppq=0), rate=RATE, start_frames=0
        )
