"""Project thumbnails, handled safely.

An upload is never stored or served as sent: it's size-checked, decoded
with a pixel limit (decompression bombs), turned upright from EXIF,
cropped to 16:9 and re-encoded as WebP, which leaves all metadata
(camera, location) behind. Files live under DATA_DIR/media, outside the
static files, and are served only through a signed-in view.
"""

import io
import pathlib
import uuid
import warnings

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.base import File
from django.core.files.storage import FileSystemStorage
from PIL import Image
from PIL import ImageOps

MAX_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 40_000_000  # about 7,700 x 5,200; plenty for a still
SIZE = (640, 360)
ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "GIF"})


class ThumbnailError(ValueError):
    """The upload isn't a usable image; the message says why."""


class _DataDirStorage(FileSystemStorage):
    """FileSystemStorage at DATA_DIR/media, read on every use.

    Django builds a field's storage once, at import; reading the setting
    each time keeps it right if DATA_DIR differs later (tests do this).
    """

    @property
    def base_location(self) -> str:  # pylint: disable=invalid-overridden-method
        return str(pathlib.Path(settings.DATA_DIR) / "media")

    @property
    def location(self) -> str:  # pylint: disable=invalid-overridden-method
        return str(pathlib.Path(self.base_location).resolve())


def storage() -> FileSystemStorage:
    """Private storage under DATA_DIR/media (no public URL)."""
    return _DataDirStorage(base_url=None)


def process(data: bytes) -> bytes:
    """Turns an uploaded image into a clean 640x360 WebP.

    Args:
        data: The uploaded file's bytes.

    Returns:
        WebP bytes with no metadata.

    Raises:
        ThumbnailError: Too large, not an allowed image format, too many
            pixels, or unreadable.
    """
    if len(data) > MAX_BYTES:
        raise ThumbnailError("Choose an image under 2 MB.")
    unreadable = ThumbnailError("That file isn't a readable image.")
    too_big = ThumbnailError("That image has too many pixels.")
    with warnings.catch_warnings():
        # Pillow only warns just above its own limit; refuse instead.
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            image = Image.open(io.BytesIO(data))
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise too_big from None
        except (OSError, SyntaxError, ValueError):
            raise unreadable from None
        with image:
            # Opening reads only the header, so these checks run before
            # any pixels are decoded.
            if image.format not in ALLOWED_FORMATS:
                raise ThumbnailError("Use a JPEG, PNG, WebP or GIF image.")
            if image.width * image.height > MAX_PIXELS:
                raise too_big
            try:
                upright = ImageOps.exif_transpose(image).convert("RGB")
            except (OSError, SyntaxError, ValueError):
                raise unreadable from None
            cropped = ImageOps.fit(upright, SIZE, Image.Resampling.LANCZOS)
    out = io.BytesIO()
    cropped.save(out, format="WEBP", quality=82, method=4)
    return out.getvalue()


def save_upload(upload: "File[bytes]") -> "ContentFile[bytes]":
    """Processes an upload into a uniquely named file to store.

    Raises:
        ThumbnailError: See `process`.
    """
    if upload.size is not None and upload.size > MAX_BYTES:
        raise ThumbnailError("Choose an image under 2 MB.")
    data = upload.read(MAX_BYTES + 1)
    return ContentFile(process(data), name=f"{uuid.uuid4().hex}.webp")
