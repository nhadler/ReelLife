"""Markers in a Standard MIDI File.

Any DAW can put its markers in a MIDI file export; Cubase is the usual
case. Marker meta-events give a name and a start only, so cues are IN
and OUT pairs or bare numbers, as with Pro Tools markers (cues/importers/
markers.py). Positions are ticks, turned into time with the file's tempo
map. Tick 0 is the file's SMPTE offset when it has one, else the
project start the dialog asks for.

The file is read here rather than by mido, which reads variable-length
numbers without a length limit (quadratic on a run of 0xFF bytes) and
builds an object for every note, both slow enough on a hostile 10 MB
file to tie up a worker. This reader walks every event as mido would,
refusing what mido refuses, but keeps only the few meta-events cues need.
"""

import dataclasses
from fractions import Fraction
import math

from mido.messages import specs

from cues import timecode
from cues.importers import base
from cues.importers import markers

_SMPTE_DIVISION = 0x8000
_MICROSECONDS = 1_000_000
_DEFAULT_TEMPO = 500_000  # 120 bpm, until the file sets one
# The SMF spec's limit: four bytes hold 0x0FFFFFFF.
_MAX_NUMBER_BYTES = 4

_META = 0xFF
_SYSEX = (0xF0, 0xF7)
_MARKER = 0x06
_CUE_MARKER = 0x07
_SET_TEMPO = 0x51
_SMPTE_OFFSET = 0x54
_KEPT = frozenset({_MARKER, _CUE_MARKER, _SET_TEMPO, _SMPTE_OFFSET})


class _DamagedError(Exception):
    """The bytes don't follow the Standard MIDI File structure."""


@dataclasses.dataclass(frozen=True)
class _Meta:
    """A meta-event this importer uses, at its absolute tick."""

    tick: int
    kind: int
    data: bytes


def _frames(seconds: Fraction, rate: timecode.Rate) -> int:
    """Real seconds to frames, rounding halves up."""
    return math.floor(seconds * rate.fps + Fraction(1, 2))


def _offset(data: bytes, rate: timecode.Rate) -> int:
    """An smpte_offset event's hh:mm:ss:ff (low 5 bits: hours) in frames.

    Raises:
        timecode.TimecodeError: Cut short, or not a time at this rate.
    """
    if len(data) < 4:
        raise timecode.TimecodeError("it is cut short")
    text = f"{data[0] & 0x1F:02d}:{data[1]:02d}:{data[2]:02d}:{data[3]:02d}"
    return timecode.parse(text, rate)


class _Reader:
    """Reads one track chunk's events."""

    def __init__(self, data: bytes, start: int, end: int) -> None:
        self.data = data
        self.pos = start
        self.end = end

    def byte(self) -> int:
        if self.pos >= self.end:
            raise _DamagedError("an event runs past the end of its track")
        value = self.data[self.pos]
        self.pos += 1
        return value

    def number(self) -> int:
        """A variable-length quantity: 7 bits a byte, at most 4 bytes."""
        value = 0
        for _ in range(_MAX_NUMBER_BYTES):
            byte = self.byte()
            value = (value << 7) | (byte & 0x7F)
            if byte < 0x80:
                return value
        raise _DamagedError("a number in it is longer than four bytes")

    def skip(self, count: int) -> bytes:
        if count > self.end - self.pos:
            raise _DamagedError("an event runs past the end of its track")
        self.pos += count
        return self.data[self.pos - count : self.pos]


def _track(reader: _Reader) -> list[_Meta]:
    """The kept meta-events of one track, with absolute ticks."""
    found = []
    tick = 0
    running: int | None = None
    while reader.pos < reader.end:
        tick += reader.number()
        status = reader.byte()
        if status < 0x80:
            # Running status: this byte is the first data byte.
            if running is None:
                raise _DamagedError("running status without a status byte")
            reader.pos -= 1
            status = running
        elif status != _META:
            running = status
        if status == _META:
            kind = reader.byte()
            data = reader.skip(reader.number())
            if kind in _KEPT:
                found.append(_Meta(tick, kind, data))
        elif status in _SYSEX:
            reader.skip(reader.number())
        elif status in specs.SPEC_BY_STATUS:
            data = reader.skip(specs.SPEC_BY_STATUS[status]["length"] - 1)
            if any(byte > 0x7F for byte in data):
                raise _DamagedError("a data byte is out of range")
        else:
            raise _DamagedError(f"undefined status byte 0x{status:02x}")
    return found


def _read(data: bytes) -> tuple[int, list[_Meta]]:
    """The ticks per beat and the kept meta-events in playback order.

    Raises:
        _DamagedError: Not a Standard MIDI File, or a damaged one.
    """
    if data[:4] != b"MThd" or len(data) < 14:
        raise _DamagedError("MThd not found")
    header_size = int.from_bytes(data[4:8])
    if header_size < 6 or 8 + header_size > len(data):
        raise _DamagedError("the header is cut short")
    tracks = int.from_bytes(data[10:12])
    ticks_per_beat = int.from_bytes(data[12:14])
    pos = 8 + header_size
    events: list[_Meta] = []
    for _ in range(tracks):
        if data[pos : pos + 4] != b"MTrk" or pos + 8 > len(data):
            raise _DamagedError("a track is missing")
        size = int.from_bytes(data[pos + 4 : pos + 8])
        pos += 8
        if size > len(data) - pos:
            raise _DamagedError("a track is longer than the file")
        events += _track(_Reader(data, pos, pos + size))
        pos += size
    # As mido.merge_tracks: by tick, keeping track order within a tick.
    events.sort(key=lambda event: event.tick)
    return ticks_per_beat, events


def parse(
    data: bytes, *, rate: timecode.Rate, start_frames: int
) -> base.ParsedFile:
    """Reads cues from a MIDI file's markers.

    Args:
        data: The .mid file's bytes.
        rate: The project's frame rate.
        start_frames: The timecode at the file's start, used when the
            file has no SMPTE offset.

    Returns:
        A row per cue, numbered by marker order.

    Raises:
        base.ImportFormatError: Not a MIDI file, a damaged one, SMPTE
            time division, or no markers.
    """
    try:
        ticks_per_beat, events = _read(data)
    except _DamagedError as exc:
        raise base.ImportFormatError(
            f"Not a readable MIDI file: {exc}."
        ) from exc
    if ticks_per_beat == 0 or ticks_per_beat & _SMPTE_DIVISION:
        raise base.ImportFormatError(
            "The MIDI file counts time in SMPTE frames, not beats. Export "
            "it again with a beats-based (PPQ) resolution."
        )
    warnings: list[str] = []
    start = start_frames
    micros = Fraction(0)
    tempo = _DEFAULT_TEMPO
    tick = 0
    placed: list[tuple[int, Fraction, str]] = []
    for event in events:
        micros += Fraction((event.tick - tick) * tempo, ticks_per_beat)
        tick = event.tick
        if event.kind == _SET_TEMPO:
            if len(event.data) < 3:
                raise base.ImportFormatError(
                    "Not a readable MIDI file: a tempo change is cut short."
                )
            tempo = int.from_bytes(event.data[:3])
        elif event.kind == _SMPTE_OFFSET:
            try:
                start = _offset(event.data, rate)
            except timecode.TimecodeError as exc:
                warnings.append(f"The file's SMPTE offset is unusable: {exc}")
        else:
            # mido's default charset for text events.
            name = event.data.decode("latin-1")
            placed.append((len(placed) + 1, micros / _MICROSECONDS, name))
    if not placed:
        raise base.ImportFormatError(
            "The MIDI file has no markers. Tick the option to include "
            "markers when exporting it."
        )
    found = [
        markers.Marker.named(number, start + _frames(at, rate), name)
        for number, at, name in placed
    ]
    return base.ParsedFile(
        source="MIDI file markers",
        rows=markers.cue_rows(found, warnings, unit="Marker"),
        warnings=warnings,
    )
