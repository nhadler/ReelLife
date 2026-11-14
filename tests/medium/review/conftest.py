"""Fixtures for the review link tests: a cue with a bounce, and its link."""

from collections.abc import Iterator

import freezegun
import pytest

from cues import models
from cues.services import review
from tests import factories
from tests.medium.review.setup import FILE_LINK


@pytest.fixture(autouse=True)
def fixture_today() -> Iterator[None]:
    with freezegun.freeze_time("2026-11-13 18:00:00"):
        yield


@pytest.fixture(name="cue")
def fixture_cue() -> models.Cue:
    """1m01 at 24 fps, IN 01:00:10:00, with v1's bounce on Dropbox."""
    film = factories.ProjectFactory.create(title="Night Ferry", frame_rate="24")
    cue = factories.CueFactory.create(
        project=film, number="1m01", title="Main Title"
    )
    version = factories.CueVersionFactory.create(
        cue=cue, label="v1", file_link=FILE_LINK
    )
    cue.current_version = version
    cue.save()
    return cue


@pytest.fixture(name="link")
def fixture_link(cue: models.Cue) -> models.ReviewLink:
    return review.create(cue, None)
