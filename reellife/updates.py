"""The installed version, and whether a newer release is out.

The scheduler asks GitHub for the latest release when it starts and each
night (`check`), and keeps the answer in the cache. Pages only read that
answer (`available`), so none waits on GitHub. REELLIFE_UPDATE_CHECK=false
turns the check off. Only the release's version and page are read; nothing
about this instance is sent beyond an ordinary HTTPS request.
"""

import dataclasses
import functools
from importlib import metadata
import json
import logging
import re
import urllib.parse

from django.conf import settings
from django.core.cache import cache
import requests

_LOGGER = logging.getLogger(__name__)
CACHE_KEY = "reellife:latest-release"
# Longer than a day, so one failed check keeps the last answer.
CACHE_SECONDS = 7 * 24 * 60 * 60
TIMEOUT = 5
MAX_BYTES = 512 * 1024
_VERSION = re.compile(r"^v?(\d{1,6})\.(\d{1,6})\.(\d{1,6})$")
_REPOSITORY = re.compile(r"^/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")


@dataclasses.dataclass(frozen=True)
class Release:
    """A published release: its version ('1.1.0') and its page."""

    version: str
    url: str


@functools.cache
def installed() -> str:
    """ReelLife's installed version, e.g. '1.0.0'."""
    try:
        # None when the installed metadata is damaged.
        return metadata.version("reellife") or "development"
    except metadata.PackageNotFoundError:  # a checkout that was never installed
        return "development"


def parse(version: str) -> tuple[int, int, int] | None:
    """A release version as numbers ('v1.2.3' -> (1, 2, 3)), else None."""
    match = _VERSION.match(version)
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def api_url(source_url: str) -> str | None:
    """GitHub's latest-release address for a repository's page, else None.

    Args:
        source_url: REELLIFE_SOURCE_URL, e.g. https://github.com/nhadler/reellife.
    """
    parts = urllib.parse.urlsplit(source_url)
    match = _REPOSITORY.match(parts.path)
    if (
        parts.scheme != "https"
        or parts.netloc.lower() != "github.com"
        or not match
    ):
        return None
    owner, repository = match.groups()
    return f"https://api.github.com/repos/{owner}/{repository}/releases/latest"


def fetch() -> Release | None:
    """The latest release on GitHub, or None if it can't be read."""
    url = api_url(settings.REELLIFE_SOURCE_URL)
    if url is None:
        return None
    try:
        with requests.get(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"ReelLife/{installed()}",
            },
            timeout=TIMEOUT,
            stream=True,
            allow_redirects=False,
        ) as reply:
            if reply.status_code != 200:
                raise ValueError(f"HTTP {reply.status_code}")
            body = reply.raw.read(MAX_BYTES + 1, decode_content=True)
        if len(body) > MAX_BYTES:
            raise ValueError("too large")
        data = json.loads(body)
        tag, page = data["tag_name"], data["html_url"]
        if not isinstance(tag, str) or parse(tag) is None:
            raise ValueError(f"not a version: {tag!r}")
        if not isinstance(page, str) or not page.startswith(
            "https://github.com/"
        ):
            raise ValueError("not a GitHub page")
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        _LOGGER.warning("update check failed: %s", exc)
        return None
    return Release(tag.removeprefix("v"), page)


def check() -> Release | None:
    """Asks GitHub for the latest release and remembers it.

    Returns:
        The release, or None when the check is off or failed (the last
        answer is kept).
    """
    if not settings.UPDATE_CHECK:
        return None
    release = fetch()
    if release is not None:
        cache.set(CACHE_KEY, dataclasses.asdict(release), CACHE_SECONDS)
        _LOGGER.info("latest release: %s", release.version)
    return release


def available() -> Release | None:
    """The remembered latest release, if it is newer than this one."""
    if not settings.UPDATE_CHECK:
        return None
    current = parse(installed())
    found = cache.get(CACHE_KEY)
    if current is None or not isinstance(found, dict):
        return None
    release = Release(str(found.get("version", "")), str(found.get("url", "")))
    latest = parse(release.version)
    return release if latest is not None and latest > current else None
