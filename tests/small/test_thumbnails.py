"""Thumbnail processing (cues.services.thumbnails)."""

import io
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
import pytest

from cues.services import thumbnails


def _image(
    fmt: str = "PNG", size: tuple[int, int] = (800, 600), **save: object
) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buffer, format=fmt, **save)
    return buffer.getvalue()


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP", "GIF"])
def test_allowed_formats_become_640x360_webp(fmt: str) -> None:
    result = _open(thumbnails.process(_image(fmt)))
    assert (result.format, result.size) == ("WEBP", (640, 360))


def test_metadata_is_dropped() -> None:
    exif = Image.Exif()
    exif[0x8827] = 400  # ISO speed: any tag will do
    exif[0x010F] = "Camera maker"
    data = _image("JPEG", exif=exif.tobytes())
    assert _open(data).getexif()
    result = _open(thumbnails.process(data))
    assert not result.getexif()
    assert "exif" not in result.info


def test_photos_are_turned_upright() -> None:
    # A portrait photo stored sideways with EXIF orientation 6 (rotate 90).
    exif = Image.Exif()
    exif[0x0112] = 6
    buffer = io.BytesIO()
    sideways = Image.new("RGB", (400, 200), (0, 0, 0))
    sideways.paste((255, 255, 255), (0, 0, 200, 200))  # white on the left
    sideways.save(buffer, format="JPEG", exif=exif.tobytes())
    result = _open(thumbnails.process(buffer.getvalue())).convert("RGB")
    # After turning upright, white is at the top of the frame.
    top = result.getpixel((320, 10))
    bottom = result.getpixel((320, 350))
    assert isinstance(top, tuple) and isinstance(bottom, tuple)
    assert sum(top) > sum(bottom)


def test_other_formats_are_refused() -> None:
    with pytest.raises(
        thumbnails.ThumbnailError, match="JPEG, PNG, WebP or GIF"
    ):
        thumbnails.process(_image("BMP"))


@pytest.mark.parametrize("data", [b"", b"not an image", _image()[:60]])
def test_unreadable_files_are_refused(data: bytes) -> None:
    with pytest.raises(thumbnails.ThumbnailError, match="readable image"):
        thumbnails.process(data)


def test_large_files_are_refused() -> None:
    with pytest.raises(thumbnails.ThumbnailError, match="under 2 MB"):
        thumbnails.process(b"x" * (thumbnails.MAX_BYTES + 1))


def test_too_many_pixels_are_refused_before_decoding() -> None:
    with (
        mock.patch.object(thumbnails, "MAX_PIXELS", 1000),
        pytest.raises(thumbnails.ThumbnailError, match="too many pixels"),
    ):
        thumbnails.process(_image(size=(100, 100)))


@pytest.mark.parametrize("limit", [5_000, 2_000])
def test_decompression_bombs_are_refused(limit: int) -> None:
    # Pillow warns just above its limit and errors at twice it.
    with (
        mock.patch.object(Image, "MAX_IMAGE_PIXELS", limit),
        pytest.raises(thumbnails.ThumbnailError, match="too many pixels"),
    ):
        thumbnails.process(_image(size=(80, 80)))


def test_save_upload_names_files_uniquely() -> None:
    first = thumbnails.save_upload(SimpleUploadedFile("a.png", _image()))
    second = thumbnails.save_upload(SimpleUploadedFile("a.png", _image()))
    assert first.name and first.name.endswith(".webp")
    assert first.name != second.name


def test_save_upload_checks_the_declared_size_first() -> None:
    upload = SimpleUploadedFile("big.png", b"x")
    upload.size = thumbnails.MAX_BYTES + 1
    with pytest.raises(thumbnails.ThumbnailError, match="under 2 MB"):
        thumbnails.save_upload(upload)
