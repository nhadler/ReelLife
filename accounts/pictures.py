"""Profile pictures from the identity provider (profile menu).

At sign-in ReelTime fetches the `picture` claim's image once, shrinks it
to a small PNG and keeps it on the user, so the strict CSP stays as it
is and pages load nothing from the provider. Only pictures the provider
itself serves are fetched (same origin as OIDC_ISSUER): a claim pointing
anywhere else could make the server request an address of someone's
choosing. Anything that goes wrong keeps the picture the user had.
"""

import hashlib
import io
import logging
from typing import Any
import urllib.parse

from django.conf import settings
from PIL import Image
from PIL import ImageOps
import requests

from accounts import models

_LOGGER = logging.getLogger(__name__)

# Bigger than the top bar needs, for sharp high-density screens.
SIZE = 96
MAX_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 4096 * 4096
TIMEOUT = 5


def _origin(url: str) -> tuple[str, str]:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme.lower(), parts.netloc.lower()


def _download(url: str) -> bytes:
    """The image's bytes. Raises ValueError for anything not a picture."""
    with requests.get(
        url, timeout=TIMEOUT, stream=True, allow_redirects=False
    ) as reply:
        if reply.status_code != 200:
            raise ValueError(f"HTTP {reply.status_code}")
        body = reply.raw.read(MAX_BYTES + 1, decode_content=True)
    if len(body) > MAX_BYTES:
        raise ValueError("too large")
    return body


def _shrink(body: bytes) -> bytes:
    """A SIZE x SIZE PNG cropped from the middle of the image."""
    with Image.open(io.BytesIO(body)) as image:
        if image.width * image.height > MAX_PIXELS:
            raise ValueError("too many pixels")
        square = ImageOps.fit(image.convert("RGBA"), (SIZE, SIZE))
    out = io.BytesIO()
    square.save(out, "PNG", optimize=True)
    return out.getvalue()


def refresh(user: models.User, url: Any) -> None:
    """Sets the user's picture from the `picture` claim; doesn't save.

    Args:
        user: The user signing in.
        url: The claim's value. No claim, or one off the provider's own
            origin, clears the picture.
    """
    if not isinstance(url, str) or _origin(url) != _origin(
        settings.OIDC_ISSUER
    ):
        user.picture, user.picture_hash = b"", ""
        return
    try:
        picture = _shrink(_download(url))
    except (
        requests.RequestException,
        OSError,
        ValueError,
        Image.DecompressionBombError,
    ) as exc:
        _LOGGER.warning("Profile picture not fetched: %s", exc)
        return
    user.picture = picture
    user.picture_hash = hashlib.sha256(picture).hexdigest()[:16]
