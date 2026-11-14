"""The shared cache's counters (reeltime.cache)."""

import datetime

from django.core.cache import cache
from django.test import Client
from django.test import override_settings
import freezegun
import pytest

pytestmark = pytest.mark.django_db


def test_a_counter_keeps_its_expiry() -> None:
    with freezegun.freeze_time("2026-11-13 12:00:00") as clock:
        cache.add("count", 1, timeout=3600)
        assert cache.incr("count") == 2
        # Django's own increment would let it lapse five minutes on.
        clock.tick(datetime.timedelta(minutes=30))
        assert cache.incr("count", 3) == 5
        clock.tick(datetime.timedelta(minutes=29, seconds=59))
        assert cache.get("count") == 5
        clock.tick(datetime.timedelta(seconds=2))
        assert cache.get("count") is None
        with pytest.raises(ValueError, match="not found"):
            cache.incr("count")


def test_the_cache_holds_many_counters() -> None:
    # Culling drops live counters, so it must be out of easy reach.
    assert cache._max_entries >= 100_000  # noqa: SLF001  # pylint: disable=protected-access


@override_settings(RATELIMIT_BREAKGLASS="2/h", BREAKGLASS_ENABLED=True)
def test_an_hourly_limit_lasts_the_hour(client: Client) -> None:
    url = "/auth/local/"
    with freezegun.freeze_time("2026-11-13 12:00:30") as clock:
        for _ in range(2):
            assert client.post(url, {}).status_code != 429
        assert client.post(url, {}).status_code == 429
        # Idle for ten minutes: still counted.
        clock.tick(datetime.timedelta(minutes=10))
        assert client.post(url, {}).status_code == 429
