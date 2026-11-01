"""The PDF exports: cue log, wall chart and version notes."""

from collections.abc import Callable
import datetime
from typing import Any

from django.db import DatabaseError
from django.test import Client
from django.urls import reverse
from PIL import ImageFile
import pytest

from accounts.models import Role
from cues import models
from cues.exporters import cue_log
from cues.exporters import pdf
from cues.exporters import version_notes
from cues.exporters import wall_chart
from cues.services import demo

pytestmark = pytest.mark.django_db

TODAY = datetime.date(2026, 10, 30)


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    return demo.build_demo().project


@pytest.fixture(name="html")
def fixture_html(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Each PDF's HTML, rendered instead of the PDF."""
    pages: list[str] = []

    def fake(template: str, context: dict[str, Any]) -> bytes:
        pages.append(pdf.html(template, context))
        return b"%PDF-fake"

    monkeypatch.setattr(pdf, "render", fake)
    return pages


def test_real_pdfs_render(
    project: models.Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetched: list[str] = []
    real_fetch = pdf._AppFonts.fetch  # pylint: disable=protected-access

    def spy(self: Any, url: str, headers: Any = None) -> Any:
        response = real_fetch(self, url, headers)
        fetched.append(url.rsplit("/", 1)[-1])
        return response

    monkeypatch.setattr(pdf._AppFonts, "fetch", spy)  # pylint: disable=protected-access
    log = cue_log.build(project, ["number", "title", "duration"], totals=True)
    for body in (
        cue_log.to_pdf(log, TODAY),
        wall_chart.wall_chart(project, today=TODAY),
        version_notes.to_pdf(project, all_versions=True, today=TODAY),
    ):
        assert body.startswith(b"%PDF-")
    # The app's own fonts were loaded, not the system's.
    assert any(name.startswith("instrument-sans") for name in fetched)
    assert any(name.startswith("ibm-plex-mono") for name in fetched)


def test_fetcher_serves_only_the_app_fonts() -> None:
    fetch = pdf._AppFonts().fetch  # pylint: disable=protected-access
    font = next(pdf._FONTS.glob("*.woff2"))  # pylint: disable=protected-access
    assert fetch(font.as_uri()).read() == font.read_bytes()
    for url in (
        "https://example.com/font.woff2",
        "file:///etc/passwd",
        (font.parent / ".." / "app.css").as_uri(),
        font.with_suffix(".css").as_uri(),
    ):
        with pytest.raises(pdf.FetchRefusedError):
            fetch(url)


def test_pillow_still_refuses_truncated_images() -> None:
    assert ImageFile.LOAD_TRUNCATED_IMAGES is False


def test_cue_log_pdf(project: models.Project, html: list[str]) -> None:
    log = cue_log.build(
        project, ["number", "title"], totals=True, show_progress=True
    )
    cue_log.to_pdf(log, TODAY)
    page = html[0]
    assert "Cue Log" in page and "1m01" in page
    assert "Reel 1" in page and "Total" in page


def test_wall_chart(project: models.Project, html: list[str]) -> None:
    first = project.cues.get(number="1m01")
    wall_chart.wall_chart(project, today=TODAY)
    page = html[0]
    assert "Wall Chart" in page and "1m01" in page and "3m02" in page
    assert first.status is not None
    assert f'fill="{first.status.color}"' in page
    wall_chart.wall_chart(project, cue_ids=[first.pk], today=TODAY)
    assert "1m01" in html[1] and "3m02" not in html[1]
    wall_chart.wall_chart(project, cue_ids=[0], today=TODAY)
    assert "No cues." in html[2]


def test_wall_chart_cue_without_status_or_times(
    project: models.Project, html: list[str]
) -> None:
    project.cues.filter(number="1m01").update(
        status=None, tc_in=None, current_version=None
    )
    wall_chart.wall_chart(project, today=TODAY)
    assert 'fill="#9ca3af"' in html[0]


def test_version_notes(project: models.Project, html: list[str]) -> None:
    version_notes.to_pdf(project, today=TODAY)
    current = html[0]
    assert "Version Notes" in current and "Re-timed to c1030." in current
    version_notes.to_pdf(project, all_versions=True, today=TODAY)
    assert "Version Notes (All Versions)" in html[1]
    models.CueVersion.objects.update(
        change_notes="", revision_notes="", notes=""
    )
    version_notes.to_pdf(project, today=TODAY)
    assert "No version notes yet." in html[2]


# The dialog and download


@pytest.fixture(name="viewer")
def fixture_viewer(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.VIEWER)


def test_dialog_offers_each_report(
    viewer: Client, project: models.Project
) -> None:
    html = viewer.get(
        reverse("cues:export", args=[project.pk])
    ).content.decode()
    for label in ("Cue Log", "Wall Chart", "Version Notes", "PDF"):
        assert label in html
    assert 'value="cue_log" id="id_report_0" checked' in html


@pytest.mark.parametrize(
    ("query", "name"),
    [
        (
            {
                "report": "cue_log",
                "format": "pdf",
                "preset": "custom",
                "columns": ["number"],
            },
            "Cue Log",
        ),
        ({"report": "wall_chart"}, "Wall Chart"),
        ({"report": "version_notes", "all_versions": "on"}, "Version Notes"),
    ],
)
def test_pdf_downloads(
    viewer: Client,
    project: models.Project,
    html: list[str],
    query: dict[str, Any],
    name: str,
) -> None:
    del html
    response = viewer.get(
        reverse("cues:export_download", args=[project.pk]),
        {"preset": "custom", "scope": "all", **query},
    )
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content == b"%PDF-fake"
    disposition = response["Content-Disposition"]
    assert f"DEMO101 The Lighthouse Keeper {name} " in disposition
    assert disposition.endswith('.pdf"')


def test_shown_cues_only(
    viewer: Client, project: models.Project, html: list[str]
) -> None:
    cue = project.cues.get(number="3m02")
    viewer.get(
        reverse("cues:export_download", args=[project.pk]),
        {
            "report": "wall_chart",
            "preset": "custom",
            "scope": "shown",
            "ids": str(cue.pk),
        },
    )
    assert "3m02" in html[0] and "1m01" not in html[0]


def test_pdf_dates_default_without_a_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> None:
        raise DatabaseError("no database")

    monkeypatch.setattr(models.AppSettings, "load", broken)
    page = pdf.html(
        "cues/pdf/header.html",
        {"what": "Wall Chart", "today": datetime.date(2026, 9, 17)},
    )
    assert "09/17/2026" in page
