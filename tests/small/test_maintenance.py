"""Snapshots, pruning and scheduling (reellife.maintenance)."""

import datetime
import pathlib
import sqlite3
import stat

import pytest

from reellife import maintenance

UTC = datetime.UTC


def _database(path: pathlib.Path, rows: int = 3) -> pathlib.Path:
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE cue (number TEXT)")
        db.executemany(
            "INSERT INTO cue VALUES (?)", [(f"1m{i:02d}",) for i in range(rows)]
        )
    return path


def test_snapshot_is_a_consistent_copy(tmp_path: pathlib.Path) -> None:
    source = _database(tmp_path / "live.sqlite3")
    path = maintenance.take_snapshot(
        source, tmp_path / "snaps", datetime.date(2026, 11, 2), keep=14
    )
    assert path == tmp_path / "snaps" / "reellife-20261102.sqlite3"
    with sqlite3.connect(path) as copy:
        assert copy.execute("SELECT count(*) FROM cue").fetchone() == (3,)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "snaps").stat().st_mode) == 0o700
    assert not list((tmp_path / "snaps").glob("*.partial"))


def test_snapshot_same_day_replaces(tmp_path: pathlib.Path) -> None:
    source = _database(tmp_path / "live.sqlite3")
    day = datetime.date(2026, 11, 2)
    maintenance.take_snapshot(source, tmp_path, day, keep=14)
    with sqlite3.connect(source) as db:
        db.execute("INSERT INTO cue VALUES ('9m99')")
    path = maintenance.take_snapshot(source, tmp_path, day, keep=14)
    assert path is not None
    with sqlite3.connect(path) as copy:
        assert copy.execute("SELECT count(*) FROM cue").fetchone() == (4,)


def test_no_snapshot_before_the_database_exists(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "missing.sqlite3"
    assert (
        maintenance.take_snapshot(
            missing, tmp_path, datetime.date(2026, 1, 1), keep=14
        )
        is None
    )
    assert not missing.exists()


def test_prune_keeps_the_newest(tmp_path: pathlib.Path) -> None:
    for day in range(1, 18):
        (tmp_path / f"reellife-202611{day:02d}.sqlite3").touch()
    (tmp_path / "notes.txt").touch()
    deleted = maintenance.prune_snapshots(tmp_path, keep=14)
    assert sorted(p.name for p in deleted) == [
        "reellife-20261101.sqlite3",
        "reellife-20261102.sqlite3",
        "reellife-20261103.sqlite3",
    ]
    assert len(list(tmp_path.glob("reellife-*"))) == 14
    assert (tmp_path / "notes.txt").exists()


def _start(source: pathlib.Path, folder: pathlib.Path, second: int) -> str:
    """A start with migrations pending, `second`s after 9:00."""
    now = datetime.datetime(2026, 11, 2, 9, 0, second, tzinfo=UTC)
    return maintenance.snapshot_before_migrate(source, folder, now).name


def test_pre_migration_snapshots_are_named_apart_from_nightly_ones(
    tmp_path: pathlib.Path,
) -> None:
    source = _database(tmp_path / "live.sqlite3")
    folder = tmp_path / "snaps"
    nightly = maintenance.take_snapshot(
        source, folder, datetime.date(2026, 11, 2), keep=14
    )
    assert _start(source, folder, 0) == "before-migrate-20261102-090000.sqlite3"
    path = folder / "before-migrate-20261102-090000.sqlite3"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with sqlite3.connect(path) as copy:
        assert copy.execute("SELECT count(*) FROM cue").fetchone() == (3,)
    # The nightly run the same day neither replaces nor prunes it.
    maintenance.take_snapshot(
        source, folder, datetime.date(2026, 11, 2), keep=1
    )
    assert path.exists()
    assert nightly is not None and nightly.exists()
    assert not list(folder.glob("*.partial"))


def test_restart_loop_keeps_the_pre_upgrade_copy(
    tmp_path: pathlib.Path,
) -> None:
    """A failing migration restarts the service over and over."""
    source = _database(tmp_path / "live.sqlite3")
    folder = tmp_path / "snaps"
    first = _start(source, folder, 0)
    for second in range(1, 10):  # nothing changed: no new copies
        assert _start(source, folder, second) == first
    assert [p.name for p in folder.iterdir()] == [first]


def test_pre_migration_snapshots_keep_the_newest_five(
    tmp_path: pathlib.Path,
) -> None:
    source = _database(tmp_path / "live.sqlite3")
    folder = tmp_path / "snaps"
    folder.mkdir()
    (folder / "before-restore-20261101-120000.sqlite3").touch()
    names = []
    for second in range(7):
        with sqlite3.connect(source) as db:  # a migration got further
            db.execute("INSERT INTO cue VALUES (?)", (f"2m{second:02d}",))
        names.append(_start(source, folder, second))
    assert sorted(p.name for p in folder.glob("before-migrate-*")) == names[2:]
    assert (folder / "before-restore-20261101-120000.sqlite3").exists()


def _at(*parts: int) -> datetime.datetime:
    year, month, day, hour, minute = parts
    return datetime.datetime(year, month, day, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (_at(2026, 11, 2, 1, 0), _at(2026, 11, 2, 3, 0)),
        (_at(2026, 11, 2, 3, 0), _at(2026, 11, 3, 3, 0)),
        (_at(2026, 11, 2, 23, 59), _at(2026, 11, 3, 3, 0)),
        (_at(2026, 12, 31, 4, 0), _at(2027, 1, 1, 3, 0)),
    ],
)
def test_next_run(now: datetime.datetime, expected: datetime.datetime) -> None:
    assert maintenance.next_run(now, 3) == expected


def test_run_forever_runs_daily_and_survives_failures() -> None:
    times = iter(
        datetime.datetime(2026, 11, 2, h, 0, tzinfo=UTC)
        for h in (1, 3, 4, 5, 6)
    )
    waits: list[float] = []
    runs: list[datetime.datetime] = []

    def wait(seconds: float) -> bool:
        waits.append(seconds)
        return len(waits) > 2

    def job(now: datetime.datetime) -> None:
        runs.append(now)
        if len(runs) == 1:
            raise RuntimeError("disk full")

    maintenance.run_forever(
        clock=lambda: next(times), wait=wait, job=job, hour=3
    )
    assert waits[0] == 2 * 3600  # 1:00 -> 3:00
    assert len(runs) == 2  # the failure didn't stop the loop
