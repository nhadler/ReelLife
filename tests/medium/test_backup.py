"""Backup validation, restore and round-trip tests."""

from collections.abc import Callable, Iterator
import contextlib
import datetime
import io
import json
import os
import pathlib
import sqlite3
from typing import Any
import zipfile

from django.contrib.contenttypes.models import ContentType
from django.core import serializers
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import DEFAULT_DB_ALIAS
from django.db import connections
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
import pytest

from accounts.models import AuditAction
from accounts.models import AuditEvent
from accounts.models import Role
from cues import models
from cues.exporters import backup
from cues.services import demo
from reellife import maintenance
from tests import factories

pytestmark = pytest.mark.django_db

NOW = datetime.datetime(2026, 10, 30, 12, 0, tzinfo=datetime.UTC)


@pytest.fixture(autouse=True)
def fixture_data_dir(settings: Any, tmp_path: pathlib.Path) -> None:
    settings.DATA_DIR = tmp_path


def _made() -> bytes:
    out = io.BytesIO()
    backup.write(out, NOW)
    return out.getvalue()


def _rows_without_passwords() -> list[dict[str, Any]]:
    rows = list(backup._rows())  # pylint: disable=protected-access
    for row in rows:
        row["fields"].pop("password", None)
    restored: list[dict[str, Any]] = json.loads(json.dumps(rows, default=str))
    return restored


def _wipe_projects() -> None:
    """What a fresh install looks like: no projects (users may exist)."""
    for model in reversed(backup._models()):  # pylint: disable=protected-access
        if model._meta.app_label == "cues":  # noqa: SLF001
            model._default_manager.all().delete()  # noqa: SLF001 pylint: disable=protected-access


@contextlib.contextmanager
def _file_database(path: pathlib.Path) -> Iterator[None]:
    """Swaps this thread's database for a freshly migrated file.

    The suite's database lives in memory, inside each test's
    transaction. This one is a new install's: a WAL file, as in
    production, where other connections can write meanwhile.
    """
    original = connections[DEFAULT_DB_ALIAS]
    fresh = connections.create_connection(DEFAULT_DB_ALIAS)
    fresh.settings_dict = {**original.settings_dict, "NAME": str(path)}
    connections[DEFAULT_DB_ALIAS] = fresh
    try:
        call_command("migrate", verbosity=0)
        yield
    finally:
        fresh.close()
        connections[DEFAULT_DB_ALIAS] = original
        ContentType.objects.clear_cache()  # ids from the other database


def _cue_numbers(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        rows = json.loads(archive.read("data.json"))
    return [r["fields"]["number"] for r in rows if r["model"] == "cues.cue"]


def _zip(entries: dict[str, bytes | str]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return out.getvalue()


def _manifest(**overrides: Any) -> str:
    manifest = {
        "format": backup.FORMAT,
        "version": backup.VERSION,
        "schema": backup.schema(),
    }
    manifest.update(overrides)
    return json.dumps(manifest)


def test_round_trip_gives_identical_data(tmp_path: pathlib.Path) -> None:
    project = demo.build_demo().project
    media = tmp_path / "media" / "thumbnails"
    media.mkdir(parents=True)
    name = "0123456789abcdef0123456789abcdef.webp"
    (media / name).write_bytes(b"RIFFwebp")
    (media / "stray.txt").write_text("not a thumbnail")
    project.thumbnail.name = f"thumbnails/{name}"
    project.save()
    factories.UserFactory.create(username="nick", password="hunter2")
    before = _rows_without_passwords()
    data = _made()

    _wipe_projects()
    (media / name).unlink(missing_ok=True)  # project delete may remove it
    counts = backup.restore(backup.read(data))

    assert _rows_without_passwords() == before
    assert counts["cues.cue"] == 40
    assert (media / name).read_bytes() == b"RIFFwebp"
    nick = models.Project.objects.get().pk  # still one project
    assert nick == project.pk


def test_passwords_and_totp_stay_out(django_user_model: Any) -> None:
    factories.UserFactory.create(username="admin", password="secret-pass")
    data = _made()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        rows = json.loads(archive.read("data.json"))
        manifest = json.loads(archive.read("manifest.json"))
    users = [r for r in rows if r["model"] == "accounts.user"]
    assert users and all(r["fields"]["password"].startswith("!") for r in users)
    assert not any(r["model"].startswith("otp_totp") for r in rows)
    assert manifest["schema"] == backup.schema()
    assert manifest["counts"]["accounts.user"] == len(users)
    del django_user_model


def test_backup_reads_one_snapshot_while_others_write(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live = tmp_path / "live.sqlite3"
    with _file_database(live):
        factories.CueFactory.create(number="1m01")
        table = models.Cue._meta.db_table  # noqa: SLF001
        serialize = serializers.serialize

        def edit_meanwhile(*args: Any, **kwargs: Any) -> Any:
            rows = serialize(*args, **kwargs)
            # Another worker renames the cue mid-backup. It must neither
            # wait for the backup nor show up in it.
            with sqlite3.connect(live, timeout=0) as other:
                other.execute(f"UPDATE {table} SET number = '9m99'")  # noqa: S608  # nosec B608
            return rows

        monkeypatch.setattr(serializers, "serialize", edit_meanwhile)
        data = _made()
        monkeypatch.undo()
        assert _cue_numbers(data) == ["1m01"]
        assert models.Cue.objects.get().number == "9m99"


def test_backup_queries_dont_grow_with_history() -> None:
    def queries_for_backup_of(cues: int) -> int:
        user = factories.UserFactory.create()
        factories.CueFactory.create_batch(cues)
        models.Cue.history.update(history_user=user)  # pylint: disable=no-member
        with CaptureQueriesContext(connections[DEFAULT_DB_ALIAS]) as context:
            _made()
        return len(context.captured_queries)

    assert queries_for_backup_of(2) == queries_for_backup_of(10)


def test_restore_needs_a_fresh_install() -> None:
    demo.build_demo()
    with pytest.raises(backup.BackupError, match="already has projects"):
        backup.restore(backup.read(_made()))


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"not a zip", "not a ZIP"),
        (_zip({"data.json": "[]"}), "no manifest"),
        (_zip({"manifest.json": "{", "data.json": "[]"}), "can't be read"),
        (
            _zip({"manifest.json": "[]", "data.json": "[]"}),
            "isn't a ReelLife backup",
        ),
        (
            _zip({"manifest.json": '{"format": "other"}', "data.json": "[]"}),
            "isn't a ReelLife backup",
        ),
        (
            _zip(
                {
                    "manifest.json": json.dumps(
                        {"format": backup.FORMAT, "version": 99}
                    ),
                    "data.json": "[]",
                }
            ),
            "newer than this ReelLife",
        ),
        (
            _zip(
                {
                    "manifest.json": json.dumps(
                        {
                            "format": backup.FORMAT,
                            "version": 1,
                            "schema": {"cues": "0001"},
                        }
                    ),
                    "data.json": "[]",
                }
            ),
            "different ReelLife version",
        ),
        (
            _zip({"manifest.json": "{}", "data.json": "[]", "../evil.py": "x"}),
            "unexpected file",
        ),
        (
            _zip(
                {
                    "manifest.json": "{}",
                    "data.json": "[]",
                    "media/thumbnails/x.png": "x",
                }
            ),
            "unexpected file",
        ),
    ],
)
def test_bad_backups_are_refused(data: bytes, message: str) -> None:
    with pytest.raises(backup.BackupError, match=message):
        backup.read(data)


def test_rows_must_be_ones_a_backup_holds() -> None:
    for rows in (
        '{"a": 1}',
        '[{"model": "auth.permission", "fields": {}}]',
        "[1]",
    ):
        data = _zip({"manifest.json": _manifest(), "data.json": rows})
        with pytest.raises(backup.BackupError, match="doesn't restore"):
            backup.read(data)


def test_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _zip({"manifest.json": _manifest(), "data.json": "[]"})
    monkeypatch.setattr(backup, "_MAX_ENTRIES", 1)
    with pytest.raises(backup.BackupError, match="too many files"):
        backup.read(data)
    monkeypatch.setattr(backup, "_MAX_ENTRIES", 10)
    monkeypatch.setattr(backup, "_MAX_UNCOMPRESSED", 3)
    with pytest.raises(backup.BackupError, match="too large"):
        backup.read(data)


def test_restore_into_a_fresh_install_never_reuses_deleted_ids(
    tmp_path: pathlib.Path,
) -> None:
    kept = factories.CueFactory.create()
    gone = factories.CueFactory.create(
        project=kept.project, picture=kept.picture
    )
    gone_pk = gone.pk
    gone.delete()  # its history stays
    data = _made()
    with _file_database(tmp_path / "new-server.sqlite3"):
        backup.restore(backup.read(data))
        fresh = factories.CueFactory.create(
            project=models.Project.objects.get(),
            picture=models.Picture.objects.get(),
        )
        assert fresh.pk > gone_pk
        assert not models.Cue.history.filter(  # pylint: disable=no-member
            id=fresh.pk, history_type="-"
        ).exists()


_THUMBNAIL = "0123456789abcdef0123456789abcdef.webp"


def _backup_with_a_thumbnail(media: pathlib.Path) -> bytes:
    """A backup of one project with a thumbnail, then a wipe."""
    project = factories.ProjectFactory.create()
    media.mkdir(parents=True)
    (media / _THUMBNAIL).write_bytes(b"RIFFwebp")
    project.thumbnail.name = f"thumbnails/{_THUMBNAIL}"
    project.save()
    data = _made()
    _wipe_projects()
    (media / _THUMBNAIL).unlink(missing_ok=True)
    return data


def _disk_full(*args: Any) -> None:
    del args
    raise OSError(28, "No space left on device")


@pytest.mark.parametrize(
    ("failing", "message"),
    [
        ("fsync", "couldn't be written"),  # staging, before the database
        ("replace", "couldn't be put in place"),  # inside the transaction
    ],
)
def test_thumbnail_failures_leave_a_retryable_install(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    failing: str,
    message: str,
) -> None:
    media = tmp_path / "media" / "thumbnails"
    checked = backup.read(_backup_with_a_thumbnail(media))

    monkeypatch.setattr(os, failing, _disk_full)
    with pytest.raises(backup.BackupError, match=message):
        backup.restore(checked)
    monkeypatch.undo()
    assert not models.Project.objects.exists()
    assert not list((tmp_path / "media").glob(".restore-*"))

    backup.restore(checked)  # the same backup, tried again
    assert models.Project.objects.count() == 1
    assert (media / _THUMBNAIL).read_bytes() == b"RIFFwebp"
    assert not list((tmp_path / "media").glob(".restore-*"))


def test_rows_that_dont_load() -> None:
    rows = json.dumps(
        [{"model": "cues.series", "pk": 1, "fields": {"nope": 1}}]
    )
    checked = backup.read(
        _zip({"manifest.json": _manifest(), "data.json": rows})
    )
    with pytest.raises(backup.BackupError, match="don't load"):
        backup.restore(checked)


def test_nightly_writes_and_prunes_backups(
    tmp_path: pathlib.Path, settings: Any
) -> None:
    settings.SNAPSHOT_KEEP = 2
    folder = tmp_path / "backups"
    folder.mkdir()
    for day in ("20261001", "20261002", "20261003"):
        (folder / f"reellife-{day}.zip").write_bytes(b"old")
    result = maintenance.nightly(NOW)
    assert result.backup == folder / "reellife-20261030.zip"
    assert sorted(p.name for p in folder.iterdir()) == [
        "reellife-20261003.zip",
        "reellife-20261030.zip",
    ]
    assert (
        backup.read(result.backup.read_bytes()).manifest["format"]
        == backup.FORMAT
    )
    assert result.backup.stat().st_mode & 0o777 == 0o600


def test_snapshot_before_needs_a_file_database() -> None:
    assert maintenance.snapshot_before("restore", NOW) is None  # in-memory


def test_snapshot_before_a_restore(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "live.sqlite3"
    with sqlite3.connect(source) as live:
        live.execute("create table t (x)")
    monkeypatch.setattr(maintenance, "database_path", lambda: source)
    path = maintenance.snapshot_before("restore", NOW)
    assert (
        path
        == tmp_path / "snapshots" / "before-restore-20261030-120000.sqlite3"
    )
    assert path.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(path) as copy:
        assert copy.execute("select name from sqlite_master").fetchall() == [
            ("t",)
        ]


# The admin page


@pytest.fixture(name="admin")
def fixture_admin(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.ADMIN)


def test_page_and_download(admin: Client) -> None:
    html = admin.get(reverse("cues:backup")).content.decode()
    assert "Download Backup" in html
    assert "Type RESTORE to Confirm" in html
    # Restore turns red only once the word matches (app.css).
    assert 'pattern="RESTORE"' in html
    response = admin.get(reverse("cues:backup_download"))
    assert response["Content-Type"] == "application/zip"
    assert 'filename="reellife-backup-' in response["Content-Disposition"]
    assert backup.read(response.content)
    assert AuditEvent.objects.filter(
        action=AuditAction.BACKUP_DOWNLOAD
    ).exists()


def _restore(client: Client, data: bytes, confirm: str = "RESTORE") -> Any:
    return client.post(
        reverse("cues:backup"),
        {"file": SimpleUploadedFile("b.zip", data), "confirm": confirm},
    )


def test_restore_from_the_page(admin: Client) -> None:
    demo.build_demo()
    data = _made()
    _wipe_projects()
    response = _restore(admin, data)
    html = response.content.decode()
    assert "Backup Restored" in html
    assert "cues.cue: 40" in html
    assert models.Cue.objects.count() == 40
    event = AuditEvent.objects.get(action=AuditAction.RESTORE)
    assert event.detail["rows"] > 40
    # Signed out: the users table was replaced.
    assert admin.get(reverse("cues:backup")).status_code == 302


def test_restore_form_errors(
    admin: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    html = _restore(admin, b"x", confirm="restore").content.decode()
    assert "Type RESTORE exactly." in html
    monkeypatch.setattr(backup, "MAX_UPLOAD", 0)
    assert "under 200 MB" in _restore(admin, b"x").content.decode()


def test_restore_failure_on_the_page_changes_nothing(
    admin: Client, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = _backup_with_a_thumbnail(tmp_path / "media" / "thumbnails")
    monkeypatch.setattr(os, "fsync", _disk_full)
    html = _restore(admin, data).content.decode()
    assert "couldn&#x27;t be written (No space left on device)" in html
    assert str(tmp_path) not in html
    assert not AuditEvent.objects.filter(action=AuditAction.RESTORE).exists()
    assert admin.get(reverse("cues:backup")).status_code == 200  # signed in


def test_restore_refusals_show_on_the_page(admin: Client) -> None:
    html = _restore(admin, b"not a zip").content.decode()
    assert "not a ZIP" in html
    assert models.Project.objects.count() == 0
