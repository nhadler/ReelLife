"""P1's "done when" against the composer's real sessions.

The composer can import a real Pro Tools session text, see every cue
with correct timecodes, add versions, change statuses, log a new
turnover, re-import and see which cues moved, undo a bad import, and
export a cue log to Excel. The real files stay on this machine
(fixtures/real/, kept out of git), so this skips where they're absent.
"""

import datetime
import io
import json
import warnings

import openpyxl
import pytest

from cues import models
from cues import timecode
from cues.api import edits
from cues.exporters import cue_log
from cues.importers import apply
from cues.importers import base
from cues.importers import merge
from cues.importers import protools
from cues.services import authoring
from cues.services import history
from cues.services import turnovers
from tests import factories
from tests import golden

pytestmark = pytest.mark.django_db

REAL = sorted((golden.FIXTURES / "real").glob("*.txt"))


def _project(rate: str) -> models.Project:
    """A film with reel 1 from 1:00:00:00 to 1:20:00:00 (turnover c0917)."""
    project = factories.ProjectFactory.create(frame_rate=rate)
    nominal = timecode.rate_for(rate).nominal
    turnovers.create_turnover(
        project,
        label="c0917",
        received_date=datetime.date(2026, 9, 17),
        reels=[
            turnovers.ReelEntry(
                None, "1", 3600 * nominal, 3600 * nominal + 20 * 60 * nominal
            )
        ],
    )
    return project


def _parse(path: object) -> base.ParsedFile:
    text = protools.decode(path.read_bytes())  # type: ignore[attr-defined]
    try:
        return protools.parse(text, method=protools.Method.MARKERS)
    except base.ImportFormatError:
        return protools.parse(
            text, method=protools.Method.CLIPS, track_prefixes=("",)
        )


@pytest.mark.parametrize("path", REAL, ids=lambda p: p.name)
def test_p1_done_when(path: object) -> None:  # pylint: disable=too-many-locals
    parsed = _parse(path)
    assert parsed.frame_rate is not None
    project = _project(parsed.frame_rate)
    rate = project.rate

    # Import: every cue, at the timecodes the golden file records.
    first = apply.apply(project, merge.plan(project, parsed, merge.Options()))
    expected = {
        row["number"]: (row["in_frames"], row["out_frames"])
        for row in json.loads(
            path.with_name(  # type: ignore[attr-defined]
                f"{path.name}.{parsed.source.split()[-1]}-all.json"  # type: ignore[attr-defined]
            ).read_text()
        )["rows"]
    }
    got = {cue.number: (cue.tc_in, cue.tc_out) for cue in project.cues.all()}
    assert got == expected
    assert first.created == len(expected)

    # Add a version; change a status in the grid.
    cue = project.cues.order_by("tc_in").first()
    assert cue is not None
    version = models.CueVersion(
        cue=cue, label=authoring.next_version_label(cue), tc_in=cue.tc_in
    )
    authoring.create_version(version, update_cue=False)
    composed = project.statuses.get(kind=models.StatusKind.DONE)
    edits.apply(cue, "status", composed.pk)
    cue.refresh_from_db()
    assert cue.current_version == version
    assert cue.status == composed

    # A new turnover moves reel 1 two seconds later.
    two_seconds = 2 * rate.nominal
    reel = project.pictures.get(name="1")
    previous = reel.current_version()
    assert previous is not None and previous.tc_in is not None
    turnovers.create_turnover(
        project,
        label="c1030",
        received_date=datetime.date(2026, 10, 30),
        reels=[
            turnovers.ReelEntry(
                reel,
                "1",
                previous.tc_in + two_seconds,
                (previous.tc_out or 0) + two_seconds,
            )
        ],
    )

    # Re-import with the cues moved: the preview shows which.
    moved = base.ParsedFile(
        parsed.source,
        [
            base.ImportRow(
                r.label,
                r.number,
                r.title,
                (r.tc_in or 0) + two_seconds,
                (r.tc_out or 0) + two_seconds,
                line=r.line,
            )
            for r in parsed.rows
        ],
        frame_rate=parsed.frame_rate,
        drop_frame=parsed.drop_frame,
    )
    preview = merge.plan(project, moved, merge.Options())
    assert preview.count(merge.Action.UPDATE) == len(expected)
    second = apply.apply(project, preview, filename="moved.txt")
    cue.refresh_from_db()
    assert cue.tc_in == expected[cue.number][0] + two_seconds

    # That import was a mistake: undo puts every cue back.
    history.undo(second.change_set)
    assert {
        c.number: (c.tc_in, c.tc_out) for c in project.cues.all()
    } == expected

    # Export the cue log to Excel.
    log = cue_log.build(
        project, ["number", "title", "tc_in", "tc_out", "duration"]
    )
    data = cue_log.to_xlsx(log, datetime.date(2026, 10, 31))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        sheet = openpyxl.load_workbook(io.BytesIO(data)).worksheets[0]
    cells = [str(c.value) for row in sheet.iter_rows() for c in row if c.value]
    for number in expected:
        assert number in cells
