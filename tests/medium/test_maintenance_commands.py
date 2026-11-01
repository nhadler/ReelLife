"""Nightly maintenance against the database, and its commands."""

from collections.abc import Iterator
import datetime
import io
import os
import pathlib
import signal
import sqlite3
from typing import Any
from unittest import mock

from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone
import pytest

from accounts import audit
from accounts import models
from reellife import maintenance

pytestmark = pytest.mark.django_db

NOW = datetime.datetime(2026, 11, 2, 3, 0, tzinfo=datetime.UTC)


@pytest.fixture(name="live_db")
def fixture_live_db(
    tmp_path: pathlib.Path, settings: Any
) -> Iterator[pathlib.Path]:
    """Points maintenance at a real SQLite file and a temp DATA_DIR."""
    source = tmp_path / "reellife.sqlite3"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE t (x)")
    settings.DATA_DIR = tmp_path
    with mock.patch.object(maintenance, "database_path", return_value=source):
        yield source


def test_nightly_snapshots_and_purges(live_db: pathlib.Path) -> None:
    old = audit.record(models.AuditAction.LOGOUT, actor="old")
    models.AuditEvent.objects.filter(pk=old.pk).update(
        created_at=NOW - datetime.timedelta(days=366)
    )
    audit.record(models.AuditAction.LOGOUT, actor="recent")
    expired = SessionStore()
    expired.create()
    Session.objects.filter(pk=expired.session_key).update(
        expire_date=NOW - datetime.timedelta(seconds=1)
    )

    result = maintenance.nightly(NOW)

    assert (
        result.snapshot
        == live_db.parent / "snapshots" / "reellife-20261102.sqlite3"
    )
    assert result.snapshot.exists()
    assert result.audit_events_purged == 1
    assert result.sessions_purged == 1
    assert list(models.AuditEvent.objects.values_list("actor", flat=True)) == [
        "recent"
    ]


def test_nightly_skips_snapshot_for_in_memory_database() -> None:
    assert maintenance.database_path() is None
    assert maintenance.nightly(timezone.now()).snapshot is None


@pytest.mark.parametrize(
    ("name", "path"),
    [
        ("/data/reellife.sqlite3", pathlib.Path("/data/reellife.sqlite3")),
        (":memory:", None),
        ("file:memorydb_default?mode=memory&cache=shared", None),
    ],
)
def test_sqlite_file(name: str, path: pathlib.Path | None) -> None:
    assert maintenance.sqlite_file(name) == path


def test_snapshot_dir_is_under_data_dir(settings: Any) -> None:
    settings.DATA_DIR = "/data"
    assert maintenance.snapshot_dir() == pathlib.Path("/data/snapshots")


def test_snapshot_command(live_db: pathlib.Path) -> None:
    out = io.StringIO()
    call_command("snapshot", stdout=out)
    assert "reellife-" in out.getvalue()
    assert list((live_db.parent / "snapshots").glob("reellife-*.sqlite3"))


def test_snapshot_command_before_first_migrate() -> None:
    out = io.StringIO()
    call_command("snapshot", stdout=out)
    assert "no database yet" in out.getvalue()


@pytest.fixture(name="unapplied")
def fixture_unapplied() -> str:
    """Marks a migration unapplied, as after pulling an upgrade."""
    MigrationRecorder(connection).record_unapplied("sessions", "0001_initial")
    return "sessions.0001_initial"


def test_pending_migrations(unapplied: str) -> None:
    assert maintenance.pending_migrations() == [unapplied]


def test_no_pending_migrations() -> None:
    assert maintenance.pending_migrations() == []


@pytest.mark.usefixtures("unapplied")
def test_snapshot_before_migrate_command(live_db: pathlib.Path) -> None:
    out = io.StringIO()
    call_command("snapshot", "--before-migrate", stdout=out)
    assert "before applying 1 migration(s)" in out.getvalue()
    snapshots = live_db.parent / "snapshots"
    assert [p.name for p in snapshots.iterdir()][0].startswith(
        "before-migrate-"
    )
    assert not list(snapshots.glob("reellife-*"))


def test_snapshot_before_migrate_only_when_pending(
    live_db: pathlib.Path,
) -> None:
    out = io.StringIO()
    call_command("snapshot", "--before-migrate", stdout=out)
    assert "not needed" in out.getvalue()
    assert not (live_db.parent / "snapshots").exists()


def test_snapshot_before_migrate_before_first_migrate(
    tmp_path: pathlib.Path,
) -> None:
    missing = tmp_path / "reellife.sqlite3"
    out = io.StringIO()
    with mock.patch.object(maintenance, "database_path", return_value=missing):
        call_command("snapshot", "--before-migrate", stdout=out)
    assert "no database yet" in out.getvalue()
    assert not missing.exists()


def test_nightly_command(live_db: pathlib.Path) -> None:
    del live_db
    out = io.StringIO()
    call_command("nightly", stdout=out)
    assert "purged 0 audit events and 0 sessions" in out.getvalue()


@pytest.fixture(name="restore_signals")
def fixture_restore_signals() -> Iterator[None]:
    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    yield
    for signum, handler in saved.items():
        signal.signal(signum, handler)


@pytest.mark.usefixtures("restore_signals")
def test_scheduler_stops_on_sigterm() -> None:
    def fake_run_forever(**kwargs: Any) -> None:
        assert kwargs["hour"] == 3
        os.kill(os.getpid(), signal.SIGTERM)
        assert kwargs["wait"](5) is True  # the handler set the stop event

    out = io.StringIO()
    with mock.patch.object(maintenance, "run_forever", fake_run_forever):
        call_command("run_scheduler", stdout=out)
    assert "nightly at 3:00 UTC" in out.getvalue()
    assert "Scheduler stopped." in out.getvalue()
