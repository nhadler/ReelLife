"""Golden-file checks for importers.

Each fixture's expected output lives next to it as JSON. To regenerate
after a deliberate change, run the tests with REELLIFE_UPDATE_GOLDEN=1 and
review the diff.
"""

import json
import os
import pathlib
from typing import Any

from cues import timecode
from cues.importers import base

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def describe(parsed: base.ParsedFile, rate: timecode.Rate) -> dict[str, Any]:
    """A reviewable JSON form of an importer's output."""

    def tc(frames: int | None) -> str | None:
        if frames is None:
            return None
        return timecode.format_timecode(frames, rate, leading_zero=True)

    return {
        "source": parsed.source,
        "frame_rate": parsed.frame_rate,
        "drop_frame": parsed.drop_frame,
        "warnings": parsed.warnings,
        "rows": [
            {
                "line": row.line,
                "label": row.label,
                "number": row.number,
                "title": row.title,
                "in": tc(row.tc_in),
                "out": tc(row.tc_out),
                "in_frames": row.tc_in,
                "out_frames": row.tc_out,
                **({"fields": dict(row.fields)} if row.fields else {}),
            }
            for row in parsed.rows
        ],
    }


def check(fixture: pathlib.Path, variant: str, actual: dict[str, Any]) -> None:
    """Compares `actual` with `<fixture>.<variant>.json`.

    Raises:
        AssertionError: On a difference, or if the golden file is missing.
    """
    golden = fixture.with_name(f"{fixture.name}.{variant}.json")
    if os.environ.get("REELLIFE_UPDATE_GOLDEN") == "1":
        golden.write_text(
            json.dumps(actual, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    assert golden.exists(), (
        f"no golden file {golden.relative_to(ROOT)}; run with "
        "REELLIFE_UPDATE_GOLDEN=1 and review it"
    )
    expected = json.loads(golden.read_text(encoding="utf-8"))
    assert actual == expected
