"""Excel and CSV cue list importer."""

from collections.abc import Callable, Sequence
import datetime
import io
import re
from unittest import mock
import zipfile

import hypothesis
from hypothesis import strategies as st
import openpyxl
import pytest

from cues import timecode
from cues.importers import base
from cues.importers import tabular
from tests import golden

FIXTURE = golden.FIXTURES / "cue_list.csv"
RATE = timecode.RATE_23_976


def _parse(data: bytes, **kwargs: object) -> base.ParsedFile:
    return tabular.parse(data, rate=RATE, **kwargs)  # type: ignore[arg-type]


def _xlsx(rows: Sequence[Sequence[object]]) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_golden() -> None:
    parsed = _parse(FIXTURE.read_bytes())
    golden.check(FIXTURE, "23976", golden.describe(parsed, RATE))


def test_csv_fixture_rules() -> None:
    parsed = _parse(FIXTURE.read_bytes())
    by_number = {row.number: row for row in parsed.rows}
    # Leading spaces trimmed; OUT computed from DUR; 1: and 01: hours.
    assert by_number["1m03"].tc_in == 95_079
    assert by_number["1m03"].tc_out == 95_079 + 2_457
    assert by_number["1m02"].tc_in == 90_720
    assert by_number["1m01"].fields == {
        "status": "Composed",
        "spotting_notes": "Big opening",
        "version": "v2",
    }
    # Invalid numbers are kept here; merge.plan excludes them.
    assert "bad row" in by_number
    assert [r.line for r in parsed.rows] == [2, 3, 4, 5, 6]


def test_columns_report_their_mapping() -> None:
    data = b"Cue #,Title,Comments\n1m01,A,hello\n"
    parsed = _parse(data)
    assert parsed.columns == [
        base.Column(0, "Cue #", "number"),
        base.Column(1, "Title", "title"),
        base.Column(2, "Comments", None),
    ]


def test_mapping_overrides_headers() -> None:
    data = b"Cue #,Title,Comments\n1m01,A,hello\n"
    parsed = _parse(data, mapping={2: "notes", 1: None})
    (row,) = parsed.rows
    assert (row.title, row.fields) == ("", {"notes": "hello"})


def test_mapping_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="unknown field"):
        _parse(b"Cue #\n1m01\n", mapping={0: "colour"})


def test_first_matching_column_wins() -> None:
    parsed = _parse(b"Cue #,Name,Title\n1m01,First,Second\n")
    assert parsed.rows[0].title == "First"
    assert parsed.columns[2].field is None


def test_header_found_below_a_title_block() -> None:
    data = (
        b"The Big Heist\nCue log\n"
        b"Cue Number;Start;End\n1m01;1:00:00:00;1:00:01:00\n"
    )
    (row,) = _parse(data).rows
    assert (row.number, row.tc_in, row.tc_out, row.line) == (
        "1m01",
        86_400,
        86_424,
        4,
    )


def test_no_cue_column_is_refused() -> None:
    with pytest.raises(base.ImportFormatError, match="cue number column"):
        _parse(b"Title,IN\nA,1:00:00:00\n")


def test_bad_timecodes_become_warnings() -> None:
    data = b"Cue,IN,OUT,DUR\n1m01,1:00:00:30,,\n1m02,1:00:00:00,,9:99\n"
    parsed = _parse(data)
    assert [(r.tc_in, r.tc_out) for r in parsed.rows] == [
        (None, None),
        (86_400, None),
    ]
    assert parsed.warnings == [
        "Row 2: IN frames must be < 24 at 23.976: '1:00:00:30'",
        "Row 3: DUR seconds must be < 60: '9:99'",
    ]


def test_blank_and_short_rows() -> None:
    data = b"Cue #,Title,IN\n\n,,\n1m01\n"
    (row,) = _parse(data).rows
    assert (row.number, row.title, row.tc_in) == ("1m01", "", None)


def test_seq_start() -> None:
    data = b"Cue #,Seq Start\n1m01,00:59:50:00\n"
    assert _parse(data).rows[0].seq_start == 86_160


def test_windows_1252_csv() -> None:
    data = "Cue #,Title\n1m01,Café\n".encode("cp1252")
    assert _parse(data).rows[0].title == "Café"


def test_tab_separated() -> None:
    assert _parse(b"Cue #\tTitle\n1m01\tA\n").rows[0].title == "A"


def test_unsniffable_csv_falls_back_to_commas() -> None:
    assert _parse(b"Cue #\n1m01\n").rows[0].number == "1m01"


def test_xlsx() -> None:
    data = _xlsx(
        [
            ["Spotting list"],
            [],
            ["Cue #", "Title", "IN", "DUR", "Status"],
            [
                101,
                "Numeric number",
                "1:00:00:00",
                datetime.time(0, 1, 25),
                "Composed",
            ],
            [1.0, "Float number", None, None, None],
        ]
    )
    rows = _parse(data).rows
    assert [(r.number, r.title, r.tc_in, r.tc_out, r.line) for r in rows] == [
        ("101", "Numeric number", 86_400, 86_400 + 85 * 24, 4),
        ("1", "Float number", None, None, 5),
    ]


def test_damaged_xlsx_is_refused() -> None:
    with pytest.raises(base.ImportFormatError, match="damaged"):
        _parse(b"PK\x03\x04 not really a zip")


def test_zip_that_isnt_a_workbook_is_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "hi")
    with pytest.raises(base.ImportFormatError, match="readable Excel"):
        _parse(buffer.getvalue())


def test_zip_bombs_are_refused_before_opening() -> None:
    data = _xlsx([["Cue #"], ["1m01"]])
    with (
        mock.patch.object(tabular, "_MAX_UNCOMPRESSED", 100),
        mock.patch.object(openpyxl, "load_workbook") as load,
        pytest.raises(base.ImportFormatError, match="50 MB"),
    ):
        _parse(data)
    load.assert_not_called()


@pytest.mark.parametrize("excel", [False, True])
def test_row_limit(excel: bool) -> None:
    rows = [["Cue #"]] + [[f"1m{n:02d}"] for n in range(10)]
    data = _xlsx(rows) if excel else "\n".join(r[0] for r in rows).encode()
    with mock.patch.object(tabular, "_MAX_ROWS", 3):
        assert len(_parse(data).rows) == 3


def _with_sheet_xml(data: bytes, edit: Callable[[bytes], bytes]) -> bytes:
    """The workbook with its first sheet's XML rewritten by `edit`."""
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as source,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            body = source.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                body = edit(body)
            target.writestr(info, body)
    return out.getvalue()


def _far_cell(xml: bytes) -> bytes:
    """Claims the sheet is A1:XFD20001 and fills its far corner."""
    xml = re.sub(rb'<dimension ref="[^"]+"\s*/>', b"", xml)
    xml = xml.replace(
        b"<sheetData>", b'<dimension ref="A1:XFD20001"/><sheetData>'
    )
    return xml.replace(
        b"</sheetData>",
        b'<row r="20001"><c r="XFD20001" t="inlineStr"><is><t>far</t></is>'
        b"</c></row></sheetData>",
    )


def test_a_sheet_claiming_to_be_huge_reads_only_its_cells() -> None:
    data = _with_sheet_xml(
        _xlsx([["Cue #", "Title"], ["1m01", "A"]]), _far_cell
    )
    rows = tabular.read_table(data)
    assert rows[:2] == [["Cue #", "Title"], ["1m01", "A"]]
    # Padding rows are empty; the far column is past the column limit.
    assert len(rows) == 20_001
    assert all(row == [] for row in rows[2:])


def test_a_sheet_without_dimensions_reads_up_to_the_column_limit() -> None:
    def far_cell_undeclared(xml: bytes) -> bytes:
        xml = re.sub(rb'<dimension ref="[^"]+"\s*/>', b"", xml)
        return xml.replace(
            b"</row></sheetData>",
            b'<c r="IV2" t="inlineStr"><is><t>last</t></is></c>'
            b'<c r="IW2" t="inlineStr"><is><t>past</t></is></c>'
            b"</row></sheetData>",
        )

    data = _with_sheet_xml(_xlsx([["Cue #"], ["1m01"]]), far_cell_undeclared)
    rows = tabular.read_table(data)
    assert rows[0] == ["Cue #"]
    assert rows[1][0] == "1m01"
    assert rows[1][-1] == "last"
    assert len(rows[1]) == 256


def test_too_many_cells_are_refused() -> None:
    data = _xlsx([["Cue #", "Title"], ["1m01", "A"], ["1m02", "B"]])
    with (
        mock.patch.object(tabular, "_MAX_CELLS", 5),
        pytest.raises(base.ImportFormatError, match="more than 5 filled"),
    ):
        _parse(data)


def test_oversized_csv_field_is_refused() -> None:
    data = b"Cue #,Notes\n1m01," + b"x" * 200_000 + b"\n"
    with pytest.raises(base.ImportFormatError, match="readable CSV"):
        _parse(data)


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (None, ""),
        (101.0, "101"),
        (1.5, "1.5"),
        (datetime.time(0, 1, 25), "0:01:25:00"),
        ("  1m01 ", "1m01"),
    ],
)
def test_cell_values(value: object, text: str) -> None:
    assert tabular._cell(value) == text  # noqa: SLF001 pylint: disable=protected-access


@hypothesis.settings(max_examples=150, deadline=None)
@hypothesis.given(cut=st.integers(0, 400), junk=st.binary(max_size=60))
def test_damaged_files_never_crash(cut: int, junk: bytes) -> None:
    data = FIXTURE.read_bytes()
    damaged = data[: cut % len(data)] + junk + data[cut % len(data) :]
    try:
        _parse(damaged)
    except base.ImportFormatError:
        pass
