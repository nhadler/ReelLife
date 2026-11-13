"""Full backups and restore.

A backup is a ZIP: manifest.json (format, the schema it was made with,
row counts), data.json (every row of the accounts and cues apps, history
and change sets included, in Django's serialization format) and the
project thumbnails. Two secrets are left out on purpose: the break-glass
admin's password hash and TOTP devices, so a leaked backup can't help
anyone at the emergency login; after a restore, set break-glass up again.

Restore validates everything before touching the database: archive size
and entry names, format version, the schema (it must match this
install's migrations exactly) and that every row belongs to a model a
backup contains. It only restores into an install with no projects yet,
snapshots the database first, and loads in one transaction, writing the
thumbnails before it commits, so a failure leaves nothing half done.
"""

from collections.abc import Iterator
import contextlib
import dataclasses
import datetime
import io
import itertools
import json
import os
import pathlib
import re
import shutil
import tempfile
from typing import IO, Any
import zipfile

from django.apps import apps
from django.contrib.auth.hashers import make_password
from django.core import serializers
from django.core.serializers.base import DeserializationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import DEFAULT_DB_ALIAS
from django.db import IntegrityError
from django.db import connections
from django.db import transaction
from django.db.migrations.loader import MigrationLoader
from django.db.models import Model

from cues import models
from cues.services import history
from cues.services import thumbnails

FORMAT = "reeltime-backup"
# The same format, from before the rename to ReelTime.
OLDER_FORMATS = ("reellife-backup",)
VERSION = 1
APPS = ("accounts", "cues")
MAX_UPLOAD = 200 * 1024 * 1024
_MAX_UNCOMPRESSED = 1024 * 1024 * 1024
_MAX_ENTRIES = 20_000
_CHUNK = 500
_THUMBNAIL = re.compile(r"^media/(thumbnails/[0-9a-f]{32}\.webp)$")


class _Encoder(DjangoJSONEncoder):
    """Django's encoder, but times keep their microseconds.

    DjangoJSONEncoder rounds datetimes to milliseconds; a backup must give
    back exactly what was there.
    """

    def default(self, o: Any) -> Any:
        if isinstance(o, (datetime.datetime, datetime.time)):
            return o.isoformat()
        return super().default(o)


class BackupError(ValueError):
    """The backup can't be restored; the message says why."""


def schema() -> dict[str, str]:
    """The newest migration of each backed-up app, e.g. {'cues': '0010_…'}."""
    graph = MigrationLoader(connections[DEFAULT_DB_ALIAS]).graph
    return {
        app: name for app, name in sorted(graph.leaf_nodes()) if app in APPS
    }


def _models() -> list[type[Model]]:
    """Backed-up models, ordered so that what's referred to comes first."""
    found = [
        model for app in APPS for model in apps.get_app_config(app).get_models()
    ]
    ordered = serializers.sort_dependencies(
        [(apps.get_app_config(app), None) for app in APPS]
    )
    return [m for m in ordered if m in found]


def _natural_key_relations(model: type[Model]) -> tuple[list[str], list[str]]:
    """Relations serialized by natural key: (foreign keys, many-to-many).

    Loading them with the rows saves a query per row (e.g. each history
    row's user).
    """
    meta = model._meta  # noqa: SLF001 pylint: disable=protected-access
    foreign = [
        f.name
        for f in meta.fields
        if f.remote_field is not None
        and hasattr(f.remote_field.model, "natural_key")
    ]
    many = [
        f.name
        for f in meta.many_to_many
        if hasattr(f.remote_field.model, "natural_key")
    ]
    return foreign, many


def _rows() -> Iterator[dict[str, Any]]:
    """Every backed-up row, model by model, a chunk at a time."""
    for model in _models():
        foreign, many = _natural_key_relations(model)
        objects = model._default_manager.order_by("pk")  # noqa: SLF001 pylint: disable=protected-access
        if foreign:  # select_related() with no names would follow all
            objects = objects.select_related(*foreign)
        if many:
            objects = objects.prefetch_related(*many)
        rows = objects.iterator(chunk_size=_CHUNK)
        for chunk in itertools.batched(rows, _CHUNK):
            for row in serializers.serialize(
                "python", chunk, use_natural_foreign_keys=True
            ):
                if row["model"] == "accounts.user":
                    # Never back up a usable password (break-glass admin).
                    row["fields"]["password"] = make_password(None)
                yield row


@contextlib.contextmanager
def _one_snapshot() -> Iterator[None]:
    """Makes every read inside see the database at one moment.

    Separate SELECTs in autocommit could each see a different state (a
    cue deleted between reading cues and their versions), and a backup
    mixing them may not restore. transaction.atomic() would BEGIN
    IMMEDIATE (the settings ask for it) and hold every writer up for as
    long as the backup takes; a deferred read transaction in WAL mode
    reads one snapshot and blocks no one.
    """
    connection = connections[DEFAULT_DB_ALIAS]
    if connection.in_atomic_block:
        yield  # already one transaction (e.g. inside a test)
        return
    with connection.cursor() as cursor:
        cursor.execute("BEGIN DEFERRED")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("ROLLBACK")


def _write_rows(out: IO[bytes]) -> dict[str, int]:
    """Writes every row as one JSON array, without holding them all.

    Returns:
        Rows per model label.
    """
    counts: dict[str, int] = {}
    out.write(b"[")
    with _one_snapshot():
        for row in _rows():
            label = row["model"]
            out.write(b"," if counts else b"")
            out.write(json.dumps(row, cls=_Encoder).encode())
            counts[label] = counts.get(label, 0) + 1
    out.write(b"]")
    return counts


def write(out: IO[bytes], now: datetime.datetime) -> dict[str, int]:
    """Writes a backup ZIP to `out`.

    Every row comes from one consistent read of the database, taken
    without blocking anyone who edits meanwhile.

    Args:
        out: A binary file to write to.
        now: Stamped into the manifest.

    Returns:
        Rows per model label.
    """
    media = pathlib.Path(thumbnails.storage().location)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        with archive.open("data.json", "w") as data:
            counts = _write_rows(data)
        manifest = {
            "format": FORMAT,
            "version": VERSION,
            "created_at": now.isoformat(),
            "schema": schema(),
            "counts": counts,
        }
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        folder = media / "thumbnails"
        if folder.is_dir():
            for path in sorted(folder.iterdir()):
                name = f"media/thumbnails/{path.name}"
                if _THUMBNAIL.match(name):
                    archive.write(path, name)
    return counts


@dataclasses.dataclass(frozen=True)
class Backup:
    """A checked backup, ready to restore."""

    manifest: dict[str, Any]
    data: str
    thumbnails: dict[str, bytes]


def _checked_entries(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    entries = archive.infolist()
    if len(entries) > _MAX_ENTRIES:
        raise BackupError("The backup has too many files.")
    if sum(e.file_size for e in entries) > _MAX_UNCOMPRESSED:
        raise BackupError("The backup is too large once unpacked.")
    for entry in entries:
        name = entry.filename
        if name not in ("manifest.json", "data.json") and not _THUMBNAIL.match(
            name
        ):
            raise BackupError(f"The backup has an unexpected file: {name!r}.")
    return entries


def read(data: bytes | IO[bytes]) -> Backup:
    """Opens and checks a backup without touching the database.

    Args:
        data: The ZIP's bytes, or a file holding it (an upload spooled to
            disk), which is read in place rather than copied into memory.

    Raises:
        BackupError: Not a ReelTime backup, made by a different schema,
            or with unexpected contents.
    """
    try:
        archive = zipfile.ZipFile(
            io.BytesIO(data) if isinstance(data, bytes) else data
        )
    except zipfile.BadZipFile as exc:
        raise BackupError("That isn't a ReelTime backup (not a ZIP).") from exc
    with archive:
        entries = {e.filename for e in _checked_entries(archive)}
        if not {"manifest.json", "data.json"} <= entries:
            raise BackupError("That isn't a ReelTime backup (no manifest).")
        try:
            manifest = json.loads(archive.read("manifest.json"))
            text = archive.read("data.json").decode("utf-8")
            rows = json.loads(text)
        except (ValueError, UnicodeDecodeError) as exc:
            raise BackupError("The backup's data can't be read.") from exc
        if not isinstance(manifest, dict) or manifest.get("format") not in (
            FORMAT,
            *OLDER_FORMATS,
        ):
            raise BackupError("That isn't a ReelTime backup.")
        if manifest.get("version") != VERSION:
            raise BackupError("This backup format is newer than this ReelTime.")
        if manifest.get("schema") != schema():
            raise BackupError(
                "This backup was made by a different ReelTime version "
                f"(schema {manifest.get('schema')}). Restore it on that "
                "version, then upgrade."
            )
        allowed = {m._meta.label_lower for m in _models()}  # noqa: SLF001 pylint: disable=protected-access
        if not isinstance(rows, list) or any(
            not isinstance(r, dict) or r.get("model") not in allowed
            for r in rows
        ):
            raise BackupError("The backup holds rows ReelTime doesn't restore.")
        images = {
            name: archive.read(name)
            for name in entries
            if _THUMBNAIL.match(name)
        }
    return Backup(manifest, text, images)


def _is_history(model: type[Model]) -> bool:
    return model.__name__.startswith("Historical")


def _stage_thumbnails(images: dict[str, bytes]) -> pathlib.Path:
    """Writes the thumbnails to disk before the database changes.

    They go to a hidden folder beside the real one (same file system, so
    putting them in place is a rename), synced, so running out of disk
    shows up here, while nothing has been restored yet.

    Returns:
        The staging folder.

    Raises:
        OSError: They can't be written; nothing is left behind.
    """
    media = pathlib.Path(thumbnails.storage().location)
    media.mkdir(parents=True, exist_ok=True)
    staging = pathlib.Path(tempfile.mkdtemp(prefix=".restore-", dir=media))
    try:
        for name, content in images.items():
            # read() let only media/thumbnails/<hex>.webp names through.
            with (staging / pathlib.PurePosixPath(name).name).open("wb") as out:
                out.write(content)
                out.flush()
                os.fsync(out.fileno())
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return staging


def _put_in_place(staging: pathlib.Path) -> None:
    """Moves staged thumbnails into the thumbnails folder."""
    folder = pathlib.Path(thumbnails.storage().location) / "thumbnails"
    folder.mkdir(exist_ok=True)
    for path in staging.iterdir():
        os.replace(path, folder / path.name)


def _load(data: str) -> dict[str, int]:
    """Replaces every backed-up table's rows with the backup's.

    Returns:
        Rows restored per model label.
    """
    counts: dict[str, int] = {}
    connection = connections[DEFAULT_DB_ALIAS]
    ordered = list(reversed(_models()))
    # Deleting tracked rows writes "deleted" history rows, so the history
    # tables are cleared last.
    for model in sorted(ordered, key=_is_history):
        model._default_manager.all().delete()  # noqa: SLF001 pylint: disable=protected-access
    with connection.constraint_checks_disabled():
        for obj in serializers.deserialize("json", data):
            # The history comes from the backup; don't add to it (saving
            # would otherwise record new rows).
            setattr(obj.object, "skip_history_when_saving", True)  # noqa: B010
            obj.save()
            label = obj.object._meta.label_lower  # noqa: SLF001 pylint: disable=protected-access
            counts[label] = counts.get(label, 0) + 1
    connection.check_constraints()
    # A fresh install's id counters stop at its largest live id; move them
    # past every id in the restored history, or a new cue could get a
    # deleted one's id and inherit its history.
    history.keep_ids_unique()
    return counts


def _reason(error: OSError) -> str:
    """Why a file operation failed, without the server's paths."""
    return error.strerror or type(error).__name__


def restore(backup: Backup) -> dict[str, int]:
    """Loads a checked backup into an install with no projects.

    All or nothing: the thumbnails are written (staged) first and moved
    into place inside the database transaction, so if anything fails the
    install is as it was and the restore can simply be tried again. A
    staged thumbnail already moved when a later step fails is harmless:
    it's named by its content's hash and the next try writes it again.

    The caller snapshots the database first (maintenance.snapshot_before)
    and signs the user out afterwards: the users table is replaced.

    Returns:
        Rows restored per model label.

    Raises:
        BackupError: Projects exist already, the rows don't load, or the
            thumbnails can't be written.
    """
    if models.Project.objects.exists():
        raise BackupError(
            "This ReelTime already has projects. Restore into a fresh "
            "install, so nothing here is overwritten."
        )
    try:
        staging = _stage_thumbnails(backup.thumbnails)
    except OSError as exc:
        raise BackupError(
            f"The thumbnails couldn't be written ({_reason(exc)}); nothing "
            "was restored."
        ) from exc
    try:
        with transaction.atomic():
            counts = _load(backup.data)
            _put_in_place(staging)
    except (DeserializationError, IntegrityError, ValueError) as exc:
        raise BackupError(f"The backup's rows don't load: {exc}") from exc
    except OSError as exc:
        raise BackupError(
            f"The thumbnails couldn't be put in place ({_reason(exc)}); "
            "nothing was restored."
        ) from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return counts
