"""PDFs from HTML templates, rendered by WeasyPrint.

The cue log, wall chart and version notes are Django templates styled by
pdf.css, landscape Letter. The renderer fetches nothing over the network:
fonts come from the app's own files and anything else is refused.
"""

import pathlib
from typing import Any

from django.db import DatabaseError
from django.template.loader import render_to_string
from PIL import ImageFile
import weasyprint
from weasyprint.text.fonts import FontConfiguration
from weasyprint.urls import URLFetcher
from weasyprint.urls import URLFetcherResponse

from cues import models

# Importing WeasyPrint makes Pillow accept truncated images, process-wide.
# PDFs embed no images, and uploaded thumbnails must refuse broken files
# (cues/services/thumbnails.py), so put it back.
ImageFile.LOAD_TRUNCATED_IMAGES = False

_HERE = pathlib.Path(__file__).parent
STYLES = _HERE / "pdf.css"
# The app's own fonts, which the stylesheet names by file URL.
_FONTS = (_HERE.parent / "static" / "cues" / "vendor").resolve()


class FetchRefusedError(ValueError):
    """The PDF asked for something outside the app's own fonts."""


class _AppFonts(URLFetcher):  # type: ignore[misc]  # untyped base
    """Fetches only the bundled font files; refuses everything else."""

    def fetch(self, url: str, headers: Any = None) -> URLFetcherResponse:
        """Opens a bundled font; anything else is refused."""
        del headers
        if url.startswith("file://"):
            path = pathlib.Path(url.removeprefix("file://")).resolve()
            if path.parent == _FONTS and path.suffix == ".woff2":
                return URLFetcherResponse(
                    url,
                    body=path.read_bytes(),
                    headers={"Content-Type": "font/woff2"},
                )
        raise FetchRefusedError(
            f"PDFs fetch nothing but the app's fonts: {url}"
        )


def html(template: str, context: dict[str, Any]) -> str:
    """A report's HTML, with App Settings' date formats in its context.

    Without a readable database (e.g. a build step) the defaults apply.
    """
    try:
        settings_row = models.AppSettings.load()
    except DatabaseError:
        settings_row = models.AppSettings()
    return render_to_string(
        template, {**settings_row.date_formats(), **context}
    )


def render(template: str, context: dict[str, Any]) -> bytes:
    """Renders a template to PDF bytes."""
    document = weasyprint.HTML(
        string=html(template, context), url_fetcher=_AppFonts()
    )
    fonts = FontConfiguration()  # needed for @font-face to load
    styles = weasyprint.CSS(
        string=STYLES.read_text().replace("{fonts}", _FONTS.as_uri()),
        url_fetcher=_AppFonts(),
        font_config=fonts,
    )
    body: bytes = document.write_pdf(stylesheets=[styles], font_config=fonts)
    return body
