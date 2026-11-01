"""The import dialog."""

from collections.abc import Callable
import datetime
import json
import re
from typing import Any

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from cues import defaults
from cues import models
from cues.services import history
from cues.services import imports
from cues.views import imports as views
from tests import factories
from tests import golden

pytestmark = pytest.mark.django_db

CLIPS = golden.FIXTURES / "protools_clips_23976.txt"
MARKERS = golden.FIXTURES / "protools_markers_2997df.txt"
CSV = golden.FIXTURES / "cue_list.csv"
CUBASE = golden.FIXTURES / "cubase_marker_track_linear.xml"
MIDI = golden.FIXTURES / "midi_markers_25.mid"


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    """A 23.976 film with reels 1 and 2 on one turnover."""
    project = factories.ProjectFactory.create(frame_rate="23.976")
    turnover = factories.TurnoverFactory.create(
        project=project, received_date=datetime.date(2026, 9, 17)
    )
    for n in (1, 2):
        picture = factories.PictureFactory.create(
            project=project, name=str(n), order=n
        )
        factories.PictureVersionFactory.create(
            picture=picture,
            turnover=turnover,
            tc_in=n * 86_400,
            tc_out=n * 86_400 + 28_800,
        )
    return project


def _upload(path: Any, name: str | None = None) -> SimpleUploadedFile:
    return SimpleUploadedFile(name or path.name, path.read_bytes())


def _post(
    client: Client, project: models.Project, step: str, **data: Any
) -> Any:
    data.setdefault("kind", "detect")
    data.setdefault("prefixes", "Cues")
    data.setdefault("mapping", "number_and_title")
    return client.post(reverse(f"cues:import_{step}", args=[project.pk]), data)


def _hidden(html: str, name: str) -> str:
    match = re.search(rf'name="{name}" value="([^"]*)"', html)
    assert match, name
    return str(match.group(1))


def _preview(
    client: Client, project: models.Project, path: Any, **data: Any
) -> str:
    response = _post(client, project, "preview", file=_upload(path), **data)
    assert response.status_code == 200
    return str(response.content.decode())


def _import(
    client: Client, project: models.Project, path: Any, **data: Any
) -> Any:
    html = _preview(client, project, path, **data)
    data.setdefault("include", _ticked(html))
    return _post(
        client,
        project,
        "apply",
        file=_upload(path),
        fingerprint=_hidden(html, "fingerprint"),
        **data,
    )


def test_dialog_opens_for_editors_only(
    editor: Client,
    role_client: Callable[[Role], Client],
    project: models.Project,
) -> None:
    url = reverse("cues:import", args=[project.pk])
    html = editor.get(url).content.decode()
    assert "Import Cues" in html
    assert 'hx-encoding="multipart/form-data"' in html
    assert 'name="kind" value="detect"' in html
    assert role_client(Role.VIEWER).get(url).status_code == 403


def test_project_page_has_the_import_button(
    editor: Client,
    project: models.Project,
    role_client: Callable[[Role], Client],
) -> None:
    html = editor.get(
        reverse("cues:project", args=[project.pk])
    ).content.decode()
    assert 'id="grid-import"' in html
    assert 'id="grid-import-open"' in html  # in the toolbar, not just Tools
    viewer = (
        role_client(Role.VIEWER)
        .get(reverse("cues:project", args=[project.pk]))
        .content.decode()
    )
    assert 'id="grid-import-open"' not in viewer


def test_spreadsheet_preview(editor: Client, project: models.Project) -> None:
    html = _preview(editor, project, CSV)
    assert "Spreadsheet:" in html
    assert "<strong>4 new</strong>" in html
    assert "1 row left out: the cue # doesn't fit Valid Cue Numbers" in html
    assert "Line 5: bad row Not a cue" in html
    assert "Columns" in html
    assert '<option value="spotting_notes" selected>' in html
    assert 'name="fingerprint"' in html
    assert html.count('name="include"') == 4


def test_pro_tools_preview_detects_the_file(
    editor: Client, project: models.Project
) -> None:
    html = _preview(editor, project, CLIPS)
    assert "Pro Tools clips:" in html
    assert "1m01" in html and "Main Title" in html
    assert "01:00:10:00" in html or "1:00:10:00" in html


def test_wrong_frame_rate_is_refused(editor: Client) -> None:
    project = factories.ProjectFactory.create(frame_rate="25")
    html = _preview(editor, project, MARKERS, method="markers")
    assert "but the project is at 25" in html
    assert "import-preview--ready" not in html


def test_unreadable_files(editor: Client, project: models.Project) -> None:
    html = _preview(editor, project, CSV, kind="protools")
    assert 'role="alert"' in html
    assert "import-preview--ready" not in html


def test_missing_file_and_bad_options(
    editor: Client, project: models.Project
) -> None:
    html = _post(editor, project, "preview").content.decode()
    assert "File: Choose a file to import." in html
    html = _preview(editor, project, CLIPS, start="99:99")
    assert "Project start: not a timecode" in html
    html = _preview(
        editor, project, CLIPS, mapping="title_only", start_number="abc"
    )
    assert "Use a cue # with a number" in html


def test_files_over_the_limit(
    editor: Client, project: models.Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(imports, "MAX_BYTES", 10)
    html = _preview(editor, project, CSV)
    assert "Choose a file under 10 MB." in html


def test_back_clears_the_preview(
    editor: Client, project: models.Project
) -> None:
    response = editor.get(reverse("cues:import_preview", args=[project.pk]))
    assert response.status_code == 200
    assert response.content == b""


def test_importing_creates_cues_and_can_be_undone(
    editor: Client, project: models.Project
) -> None:
    response = _import(editor, project, CSV)
    assert response.status_code == 204
    triggers = json.loads(response["HX-Trigger"])
    assert triggers["dialog:close"] is True
    assert triggers["cues:imported"]["message"].startswith(
        "Imported cue_list.csv: 4 new, 0 updated."
    )
    assert project.cues.count() == 4
    main = project.cues.get(number="1m01")
    assert main.current_version is not None
    assert main.current_version.spotting_notes == "Big opening"
    history.undo(models.ChangeSet.objects.get(label="Import cue_list.csv"))
    assert not project.cues.exists()


def test_only_ticked_rows_are_imported(
    editor: Client, project: models.Project
) -> None:
    _import(editor, project, CSV, include=["0", "2"])
    assert sorted(project.cues.values_list("number", flat=True)) == [
        "1m01",
        "1m03",
    ]


def test_updates_show_old_and_new_and_respect_the_title_box(
    editor: Client, project: models.Project
) -> None:
    reel1 = project.pictures.get(name="1")
    factories.CueFactory.create(
        project=project, picture=reel1, number="1m01", title="Old", tc_in=86_400
    )
    extra = factories.CueFactory.create(
        project=project, picture=reel1, number="1m09", title="Gone"
    )
    html = _preview(editor, project, CLIPS)
    assert "Update</span>" in html
    assert "<del>1:00:00:00</del>" in html or "<del>01:00:00:00</del>" in html
    assert "1 cue in the project isn&#x27;t in the file" in html
    assert 'name="missing" value="leave" checked' in html
    html = _preview(editor, project, CLIPS, update_titles="on")
    assert "<del>Old</del> Main Title" in html
    _import(
        editor,
        project,
        CLIPS,
        update_titles="on",
        missing="inactive",
        missing_ids=[str(extra.pk)],
    )
    main = project.cues.get(number="1m01")
    assert main.title == "Main Title"
    extra.refresh_from_db()
    assert not extra.active


def _ticked(html: str) -> list[str]:
    return re.findall(r'name="include" value="(\d+)"[^>]*checked', html)


def test_re_preview_keeps_choices_for_the_same_rows(
    editor: Client, project: models.Project
) -> None:
    html = _preview(editor, project, CSV)
    assert _ticked(html) == ["0", "1", "2", "3"]
    key = _hidden(html, "numbers_key")
    again = _preview(
        editor, project, CSV, previewed="1", numbers_key=key, include=["1"]
    )
    assert _ticked(again) == ["1"]
    other = _preview(
        editor, project, CSV, previewed="1", numbers_key="stale", include=["1"]
    )
    assert _ticked(other) == ["0", "1", "2", "3"]


def test_mapping_columns(editor: Client, project: models.Project) -> None:
    html = _preview(editor, project, CSV, **{"column-1": ""})  # ignore Title
    assert "Main Title" not in html
    html = _preview(editor, project, CSV, **{"column-1": "bogus"})
    assert "Main Title" not in html


def test_project_changes_between_preview_and_import(
    editor: Client, project: models.Project
) -> None:
    html = _preview(editor, project, CSV)
    factories.CueFactory.create(
        project=project, picture=project.pictures.get(name="1"), number="1m02"
    )
    response = _post(
        editor,
        project,
        "apply",
        file=_upload(CSV),
        fingerprint=_hidden(html, "fingerprint"),
        include=["0", "1", "2", "3"],
    )
    assert (
        views.CHANGED_SINCE.split(".", maxsplit=1)[0]
        in response.content.decode()
    )
    assert project.cues.count() == 1


def test_invalid_cues_are_reported(
    editor: Client, project: models.Project
) -> None:
    project.cue_number_pattern = defaults.ANY_CUE_NUMBER_PATTERN
    project.save()
    long = SimpleUploadedFile(
        "long.csv", f"Cue #,Title\n{'x' * 30},Long\n".encode()
    )
    html = _post(editor, project, "preview", file=long).content.decode()
    response = _post(
        editor,
        project,
        "apply",
        file=SimpleUploadedFile(
            "long.csv", f"Cue #,Title\n{'x' * 30},Long\n".encode()
        ),
        fingerprint=_hidden(html, "fingerprint"),
        include=["0"],
    )
    assert "at most 20 characters" in response.content.decode()
    assert not project.cues.exists()


def test_apply_refuses_bad_forms_and_files(
    editor: Client, project: models.Project
) -> None:
    assert (
        "Choose a file to import"
        in _post(editor, project, "apply").content.decode()
    )
    response = _post(
        editor, project, "apply", file=_upload(CSV), kind="protools"
    )
    assert 'role="alert"' in response.content.decode()


def test_cubase_imports_remember_the_start(editor: Client) -> None:
    project = factories.ProjectFactory.create(frame_rate="24")
    html = _preview(editor, project, CUBASE, start="0:00:00:00")
    assert "Cubase/Nuendo track archive:" in html
    _import(editor, project, CUBASE, start="0:00:00:00")
    project.refresh_from_db()
    assert project.daw_start == 0
    dialog = editor.get(
        reverse("cues:import", args=[project.pk])
    ).content.decode()
    assert 'value="00:00:00:00"' in dialog or 'value="0:00:00:00"' in dialog


def test_midi_imports_use_the_file_offset_and_remember_it(
    editor: Client,
) -> None:
    project = factories.ProjectFactory.create(frame_rate="25")
    html = _preview(editor, project, MIDI, start="0:00:00:00")
    assert "MIDI file markers:" in html
    _import(editor, project, MIDI, start="0:00:00:00")
    cue = project.cues.get(number="1m01")
    assert cue.tc_in == 3600 * 25 + 10 * 25
    project.refresh_from_db()
    assert project.daw_start == 0


def test_default_cubase_start_is_the_first_reel(
    project: models.Project,
) -> None:
    assert imports.default_start(project) == 86_400
    empty = factories.ProjectFactory.create()
    assert imports.default_start(empty) == 0


@pytest.mark.parametrize(
    ("name", "data", "kind"),
    [
        ("session.txt", b"", imports.Kind.PROTOOLS),
        ("markers.XML", b"", imports.Kind.CUBASE),
        ("list.csv", b"", imports.Kind.SPREADSHEET),
        ("list.xlsx", b"", imports.Kind.SPREADSHEET),
        ("download", b"PK\x03\x04", imports.Kind.SPREADSHEET),
        ("download", b"\xef\xbb\xbf<?xml version='1.0'?>", imports.Kind.CUBASE),
        ("download", b"SESSION NAME:\tX", imports.Kind.PROTOOLS),
        ("download", "SESSION NAME:".encode("utf-16"), imports.Kind.PROTOOLS),
        ("download", b"Cue #,Title", imports.Kind.SPREADSHEET),
        ("song.MID", b"", imports.Kind.MIDI),
        ("download", b"MThd\x00\x00\x00\x06", imports.Kind.MIDI),
    ],
)
def test_detect(name: str, data: bytes, kind: imports.Kind) -> None:
    assert imports.detect(name, data) == kind
