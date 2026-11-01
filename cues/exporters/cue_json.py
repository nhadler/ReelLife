"""Copy JSON.

Keyboard Maestro and SoundFlow macros read these keys by name, so the
keys and their formats are fixed: timecodes with a leading zero and no
subframes, durations as m:ss plus whole seconds, everything else as text.
See `_offset` for the two offsets.
"""

import math
from typing import Any

from cues import models
from cues import timecode
from cues.services import progress

Json = dict[str, Any]


def _tc(frames: int | None, rate: timecode.Rate) -> str:
    if frames is None:
        return ""
    return timecode.format_timecode(frames, rate, leading_zero=True)


def _duration(
    tc_in: int | None, tc_out: int | None, rate: timecode.Rate
) -> tuple[str, int | str]:
    if tc_in is None or tc_out is None:
        return "", ""
    frames = max(tc_out - tc_in, 0)
    seconds = math.floor(timecode.seconds(frames, rate) + 0.5)
    return timecode.format_duration(frames, rate), seconds


def _offset(frames: int | None, rate: timecode.Rate) -> str:
    """A signed offset, '-' only when negative: '00:00:02:12'.

    A zero offset is commonly written '00:00:00' (no frames); ReelLife
    keeps the frames for nonzero ones.
    """
    if frames is None:
        return ""
    sign = "-" if frames < 0 else ""
    return sign + _tc(abs(frames), rate)


def _picture(
    prefix: str,
    version: models.PictureVersion | None,
    cue: models.Cue,
    cue_version: models.CueVersion | None,
    rate: timecode.Rate,
) -> Json:
    """The current_picture_* or written_to_picture_* keys."""
    if version is None:
        return {
            f"{prefix}": "",
            f"{prefix}_name": "",
            f"{prefix}_version": "",
            f"{prefix}_in": "",
            f"{prefix}_out": "",
            f"{prefix}_cue_start_offset": "",
            f"{prefix}_cue_version_start_offset": "",
        }
    name, label = version.picture.name, version.turnover.label
    start = version.tc_in

    def from_start(frames: int | None) -> str:
        if frames is None or start is None:
            return ""
        return _offset(frames - start, rate)

    return {
        f"{prefix}": f"{name} - {label}" if name else label,
        f"{prefix}_name": name,
        f"{prefix}_version": label,
        f"{prefix}_in": _tc(version.tc_in, rate),
        f"{prefix}_out": _tc(version.tc_out, rate),
        f"{prefix}_cue_start_offset": from_start(cue.tc_in),
        f"{prefix}_cue_version_start_offset": from_start(
            cue_version.tc_in if cue_version else None
        ),
    }


def _season_and_episode(season: str, episode: str) -> str:
    """'1' and '101' give '101'; '2' and '3' give '203'."""
    if not season or episode.startswith(season):
        return episode
    return f"{season}{episode.zfill(2)}" if episode else season


def _names(cue: models.Cue, label: str, production_number: str) -> Json:
    """The cue number, title and version combinations."""
    with_title = f"{cue.number} {cue.title}".strip()
    with_version = f"{with_title} ({label})" if label else with_title

    def numbered(text: str) -> str:
        return f"{production_number} {text}" if production_number else text

    return {
        "cue_number": cue.number,
        "title": cue.title,
        "cue_number_and_title": with_title,
        "cue_number_and_title_with_cue_version": with_version,
        "cue_number_with_production_number": numbered(cue.number),
        "cue_number_and_title_with_production_number": numbered(with_title),
        "cue_number_and_title_with_cue_version_and_production_number": (
            numbered(with_version)
        ),
        "cue_version": label,
        "cue_version_without_v_prefix": label[1:]
        if label[:1] in ("v", "V")
        else label,
    }


def _timings(
    cue: models.Cue, version: models.CueVersion | None, rate: timecode.Rate
) -> Json:
    """Current and version timecodes, durations and the offsets between."""
    v_in = version.tc_in if version else None
    v_out = version.tc_out if version else None
    duration, duration_seconds = _duration(cue.tc_in, cue.tc_out, rate)
    v_duration, v_seconds = _duration(v_in, v_out, rate)
    facts = progress.conform(
        written_to_id=None,
        current_picture_version_id=None,
        cue_in=cue.tc_in,
        cue_out=cue.tc_out,
        version_in=v_in,
        version_out=v_out,
    )
    return {
        "in": _tc(cue.tc_in, rate),
        "out": _tc(cue.tc_out, rate),
        "duration": duration,
        "duration_seconds": duration_seconds,
        "sequencer_start": _tc(cue.seq_start, rate),
        "version_in": _tc(v_in, rate),
        "version_out": _tc(v_out, rate),
        "version_duration": v_duration,
        "version_duration_seconds": v_seconds,
        "version_sequencer_start": _tc(
            version.seq_start if version else None, rate
        ),
        "start_offset": _offset(facts.start_offset, rate),
        "duration_difference": _offset(facts.duration_difference, rate),
    }


def _project(project: models.Project) -> Json:
    """The production keys, the same for every cue."""
    series = project.series
    return {
        "frame_rate": project.frame_rate
        + (" DF" if project.drop_frame else ""),
        "series_title": series.title if series else "",
        "series_production_number": series.production_number if series else "",
        "production_number": project.production_number,
        "project_title": project.title,
        "project_sub_title": project.sub_title,
        "project_title_with_sub_title": " · ".join(
            p for p in (project.title, project.sub_title) if p
        ),
        "season": project.season,
        "episode_number": project.episode_number,
        "season_and_episode_number": _season_and_episode(
            project.season, project.episode_number
        ),
    }


def entries(
    cues: list[tuple[models.Cue, models.CueVersion | None]],
    project: models.Project,
) -> list[Json]:
    """One object per (cue, version) pair, in order.

    Args:
        cues: Cues with the version to describe (the current one, or the
            one being viewed on the details page).
        project: Their project.

    Returns:
        The objects, ready for json.dumps, keys in the macros' order.
    """
    rate = project.rate
    sequence = progress.sequence_numbers(project)
    production = _project(project)
    # Each reel's current picture, looked up once rather than per cue.
    current = progress.current_picture_versions(project)
    pictures = models.PictureVersion.objects.select_related("turnover").in_bulk(
        list(current.values())
    )
    result = []
    for count, (cue, version) in enumerate(cues, start=1):
        result.append(
            {
                "sequence_number": sequence.get(cue.pk, ""),
                "cue_count": count,
                **_names(
                    cue,
                    version.label if version else "",
                    project.production_number,
                ),
                **_timings(cue, version, rate),
                "status": cue.status.label if cue.status else "",
                **_picture(
                    "current_picture",
                    pictures.get(current.get(cue.picture_id, 0)),
                    cue,
                    version,
                    rate,
                ),
                **_picture(
                    "written_to_picture",
                    version.written_to if version else None,
                    cue,
                    version,
                    rate,
                ),
                **production,
                "custom": {},
            }
        )
    return result
