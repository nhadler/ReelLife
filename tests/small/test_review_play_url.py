"""Which File Links a review page can play, and from where."""

import pytest

from cues.services import review

RAW = "https://www.dropbox.com/scl/fi/abc/1m01.mp4?rlkey=k&raw=1"


@pytest.mark.parametrize(
    ("link", "url"),
    [
        # Dropbox's page about the file becomes the file itself.
        ("https://www.dropbox.com/scl/fi/abc/1m01.mp4?rlkey=k&dl=0", RAW),
        ("https://www.dropbox.com/scl/fi/abc/1m01.mp4?rlkey=k&dl=1", RAW),
        ("https://www.dropbox.com/scl/fi/abc/1m01.mp4?raw=1&rlkey=k", RAW),
        ("  https://dropbox.com/scl/fi/abc/1m01.mp4?rlkey=k  ", RAW),
        ("https://WWW.DROPBOX.COM/scl/fi/abc/1m01.mp4?rlkey=k", RAW),
        (
            "https://www.dropbox.com/s/abc/1m01.mp4#t=5",
            "https://www.dropbox.com/s/abc/1m01.mp4?raw=1",
        ),
        (
            "https://dl.dropboxusercontent.com/scl/fi/abc/1m01.mp4?rlkey=k",
            "https://dl.dropboxusercontent.com/scl/fi/abc/1m01.mp4?rlkey=k",
        ),
    ],
)
def test_dropbox_file_links_play(link: str, url: str) -> None:
    assert review.play_url(link) == url


@pytest.mark.parametrize(
    "link",
    [
        "",
        "/Volumes/Bounces/1m01.mov",
        "http://www.dropbox.com/scl/fi/abc/1m01.mp4",  # not https
        "https://www.dropbox.com",
        "https://www.dropbox.com/",
        "https://www.dropbox.com/scl/fo/abc/folder?rlkey=k",  # a folder
        "https://www.dropbox.com/SCL/FO/abc/folder?rlkey=k",
        "https://www.dropbox.com/sh/abc/folder",
        # The owner's own pages, transfers and the like: not share links.
        "https://www.dropbox.com/home/Films/1m01.mp4",
        "https://www.dropbox.com/preview/Films/1m01.mp4",
        "https://www.dropbox.com/t/abcdef",
        "https://www.dropbox.com/transfer/abc",
        "https://www.dropbox.com/login",
        "https://www.dropbox.com/scl/fi/abc",  # no file name
        "https://www.dropbox.com/scl/fi/abc/1m01.mp4/more",
        "https://dl.dropboxusercontent.com/home/1m01.mp4",
        "https://www.dropbox.com:8443/s/abc/1m01.mp4",
        "https://user@www.dropbox.com/s/abc/1m01.mp4",
        "https://www.dropbox.com.example.org/s/abc/1m01.mp4",
        "https://example.org/www.dropbox.com/1m01.mp4",
        "https://drive.google.com/file/d/abc/view",
        "javascript:alert(1)",
        "https://[bad/s/abc",
    ],
)
def test_other_links_do_not(link: str) -> None:
    assert review.play_url(link) == ""


def test_the_media_sources_cover_the_urls() -> None:
    # Dropbox's site redirects a raw link to a dropboxusercontent.com host.
    assert review.MEDIA_SOURCES == (
        "https://www.dropbox.com",
        "https://*.dropboxusercontent.com",
    )
