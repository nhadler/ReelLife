"""The shared cache: Django's database cache, with counters that last.

Rate limits count in the cache (django-ratelimit): a counter is made
with its window's lifetime, then incremented. Django's database cache
has no increment of its own, and the one it inherits saves the new value
with the cache's default lifetime (five minutes). An hourly limit then
forgot its count five minutes after the last request. Here an increment
keeps the counter's own expiry, and reads and writes in one transaction
(SQLite takes the write lock first), so two workers can't both read the
same count.
"""

import math
from typing import Any

from django.core.cache.backends.db import DatabaseCache
from django.db import connections
from django.db import models
from django.db import router
from django.db import transaction
from django.utils import timezone


class Cache(DatabaseCache):
    """DatabaseCache whose `incr` is atomic and keeps the key's expiry."""

    def incr(self, key: Any, delta: int = 1, version: int | None = None) -> int:
        """Adds `delta` to a counter, leaving when it expires alone.

        Raises:
            ValueError: The key isn't in the cache (or has expired).
        """
        database = router.db_for_write(self.cache_model_class)
        connection = connections[database]
        table = connection.ops.quote_name(self.cache_model_class._meta.db_table)
        stored = self.make_and_validate_key(key, version=version)
        with transaction.atomic(using=database):
            value = self.get(key, self._missing_key, version=version)
            if value is self._missing_key:
                raise ValueError(f"Key '{key}' not found")
            # The table's name is Django's own, quoted; the key is bound.
            query = f"SELECT expires FROM {table} WHERE cache_key = %s"  # noqa: S608  # nosec B608
            with connection.cursor() as cursor:
                cursor.execute(query, [stored])
                (expires,) = cursor.fetchone()
            when = models.Expression(output_field=models.DateTimeField())
            for convert in connection.ops.get_db_converters(
                when
            ) + when.get_db_converters(connection):
                expires = convert(expires, when, connection)
            left = (expires - timezone.now()).total_seconds()
            new_value = int(value) + delta
            self.set(
                key, new_value, timeout=max(1, math.ceil(left)), version=version
            )
        return new_value
