"""Nightly maintenance: database snapshots and housekeeping.

The host backs up the data directory, but a live SQLite file in WAL mode
can be copied mid-write. Each night SQLite's online backup API writes a
consistent copy to DATA_DIR/snapshots/reellife-YYYYMMDD.sqlite3 (keeping
the newest 14), which host backups pick up safely, and a full backup ZIP
(the same as an admin downloads) to DATA_DIR/backups, also keeping 14.

Each start that is about to apply migrations first writes
before-migrate-YYYYMMDD-HHMMSS.sqlite3 (keeping the newest 5), so an
upgrade can be rolled back however often the service restarts.
"""

from collections.abc import Callable
import contextlib
import dataclasses
import datetime
import filecmp
import logging
import os
import pathlib
import sqlite3

from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import DEFAULT_DB_ALIAS
from django.db import connections
from django.db.migrations.executor import MigrationExecutor

from accounts import audit
from cues.exporters import backup

_LOGGER = logging.getLogger(__name__)
_SNAPSHOT_GLOB = "reellife-[0-9]*.sqlite3"
_BACKUP_GLOB = "reellife-[0-9]*.zip"
_MIGRATE_SNAPSHOT_GLOB = "before-migrate-[0-9]*.sqlite3"
MIGRATE_SNAPSHOT_KEEP = 5


def snapshot_dir() -> pathlib.Path:
    """Where snapshots are written."""
    return pathlib.Path(settings.DATA_DIR) / "snapshots"


def sqlite_file(name: str) -> pathlib.Path | None:
    """Returns the file a SQLite database NAME refers to.

    Returns:
        The path, or None for an in-memory database (':memory:' or a
        'file:...?mode=memory' URI, as Django's test runner uses).
    """
    if name == ":memory:" or "mode=memory" in name:
        return None
    return pathlib.Path(name)


def database_path() -> pathlib.Path | None:
    """The SQLite file in use, or None for an in-memory database."""
    return sqlite_file(str(connections[DEFAULT_DB_ALIAS].settings_dict["NAME"]))


def take_snapshot(
    source: pathlib.Path,
    directory: pathlib.Path,
    today: datetime.date,
    *,
    keep: int,
) -> pathlib.Path | None:
    """Writes a consistent copy of a SQLite database, then prunes old ones.

    Args:
        source: The live database file.
        directory: Where snapshots go; created if missing.
        today: Names the snapshot; a second one the same day replaces it.
        keep: How many snapshots to keep, newest first.

    Returns:
        The snapshot's path, or None if the database doesn't exist yet
        (a fresh install before its first migrate).
    """
    if not source.exists():
        return None
    target = directory / f"reellife-{today:%Y%m%d}.sqlite3"
    _copy_database(source, target)
    prune_snapshots(directory, keep=keep)
    return target


def _copy_database(source: pathlib.Path, target: pathlib.Path) -> None:
    """Copies a live SQLite database consistently, readable by us only."""
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    partial = target.with_suffix(".partial")
    with (
        contextlib.closing(sqlite3.connect(source)) as live,
        contextlib.closing(sqlite3.connect(partial)) as copy,
    ):
        live.backup(copy)
    partial.chmod(0o600)  # every cue and the audit log are in here
    os.replace(partial, target)


def snapshot_before(event: str, now: datetime.datetime) -> pathlib.Path | None:
    """A snapshot taken before a risky change, e.g. 'restore'.

    Named apart from the nightly ones, so pruning never removes it.

    Returns:
        The path, or None for an in-memory database.
    """
    source = database_path()
    if source is None or not source.exists():
        return None
    target = snapshot_dir() / f"before-{event}-{now:%Y%m%d-%H%M%S}.sqlite3"
    _copy_database(source, target)
    return target


def pending_migrations() -> list[str]:
    """Migrations `migrate` would apply now, e.g. ['cues.0037_…'].

    Reads the default database; connecting creates its file if missing,
    so check that it exists first when that matters.
    """
    executor = MigrationExecutor(connections[DEFAULT_DB_ALIAS])
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return [f"{migration.app_label}.{migration.name}" for migration, _ in plan]


def snapshot_before_migrate(
    source: pathlib.Path,
    directory: pathlib.Path,
    now: datetime.datetime,
    *,
    keep: int = MIGRATE_SNAPSHOT_KEEP,
) -> pathlib.Path:
    """Copies the database before an upgrade migrates it.

    Each copy gets its own name (before-migrate-YYYYMMDD-HHMMSS), apart
    from the nightly ones, so neither a nightly run nor another start the
    same day overwrites the pre-upgrade copy. If the newest one already
    holds exactly this database (a start that keeps failing, so nothing
    has changed since), no new copy is made: a restart loop can't crowd
    the pre-upgrade copy out of the newest `keep`.

    Args:
        source: The live database file; it must exist.
        directory: Where snapshots go; created if missing.
        now: Names the snapshot.
        keep: How many pre-migration snapshots to keep, newest first.

    Returns:
        The snapshot that holds the database as it is now.
    """
    target = directory / f"before-migrate-{now:%Y%m%d-%H%M%S}.sqlite3"
    newest = sorted(directory.glob(_MIGRATE_SNAPSHOT_GLOB), reverse=True)
    partial = target.with_suffix(".partial")
    _copy_database(source, partial)
    if newest and filecmp.cmp(partial, newest[0], shallow=False):
        partial.unlink()
        return newest[0]
    os.replace(partial, target)
    prune_snapshots(directory, keep=keep, pattern=_MIGRATE_SNAPSHOT_GLOB)
    return target


def backup_dir() -> pathlib.Path:
    """Where nightly backup ZIPs are written."""
    return pathlib.Path(settings.DATA_DIR) / "backups"


def write_backup(
    directory: pathlib.Path, now: datetime.datetime, *, keep: int
) -> pathlib.Path:
    """Writes today's backup ZIP, then prunes old ones.

    Returns:
        The backup's path.
    """
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / f"reellife-{now:%Y%m%d}.zip"
    partial = target.with_suffix(".partial")
    with partial.open("wb") as out:
        backup.write(out, now)
    partial.chmod(0o600)
    os.replace(partial, target)
    prune_snapshots(directory, keep=keep, pattern=_BACKUP_GLOB)
    return target


def prune_snapshots(
    directory: pathlib.Path, *, keep: int, pattern: str = _SNAPSHOT_GLOB
) -> list[pathlib.Path]:
    """Deletes all but the newest `keep` snapshots (or backups).

    Returns:
        The deleted paths.
    """
    snapshots = sorted(directory.glob(pattern), reverse=True)
    stale = snapshots[keep:]
    for path in stale:
        path.unlink()
    return stale


@dataclasses.dataclass(frozen=True)
class NightlyResult:
    """What a nightly run did."""

    snapshot: pathlib.Path | None
    backup: pathlib.Path
    audit_events_purged: int
    sessions_purged: int


def nightly(now: datetime.datetime) -> NightlyResult:
    """Snapshots the database, writes a backup, clears expired data.

    Args:
        now: The current time (injected for tests).

    Returns:
        What was done.
    """
    source = database_path()
    path = None
    if source is not None:
        path = take_snapshot(
            source, snapshot_dir(), now.date(), keep=settings.SNAPSHOT_KEEP
        )
    backup_path = write_backup(backup_dir(), now, keep=settings.SNAPSHOT_KEEP)
    cutoff = now - datetime.timedelta(days=settings.AUDIT_RETENTION_DAYS)
    purged = audit.purge_before(cutoff)
    sessions, _ = Session.objects.filter(expire_date__lt=now).delete()
    result = NightlyResult(path, backup_path, purged, sessions)
    _LOGGER.info(
        "nightly maintenance: snapshot=%s backup=%s audit_purged=%d "
        "sessions_purged=%d",
        path,
        backup_path,
        purged,
        sessions,
    )
    return result


def next_run(now: datetime.datetime, hour: int) -> datetime.datetime:
    """Returns the next time it is `hour`:00 in now's time zone."""
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += datetime.timedelta(days=1)
    return candidate


def run_forever(
    *,
    clock: Callable[[], datetime.datetime],
    wait: Callable[[float], bool],
    job: Callable[[datetime.datetime], object],
    hour: int,
) -> None:
    """Runs `job` every day at `hour`:00 until `wait` reports a stop.

    Args:
        clock: Returns the current time.
        wait: Sleeps up to the given seconds; returns True to stop (e.g.
            threading.Event.wait, set from a SIGTERM handler).
        job: The work to run; failures are logged and retried next day.
        hour: Hour of the day to run.
    """
    while True:
        now = clock()
        delay = (next_run(now, hour) - now).total_seconds()
        if wait(delay):
            return
        try:
            job(clock())
        except Exception:  # pylint: disable=broad-exception-caught
            # Keep the scheduler alive; tomorrow's run may succeed.
            _LOGGER.exception("nightly maintenance failed")
