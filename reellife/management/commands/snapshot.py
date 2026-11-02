"""manage.py snapshot: copy the database now (e.g. before migrating)."""

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandParser
from django.utils import timezone

from reellife import maintenance


class Command(BaseCommand):
    """Writes a snapshot; does nothing before the first migrate."""

    help = "Write a consistent SQLite snapshot to DATA_DIR/snapshots."
    # It must see the disk before Django opens the database: from Django
    # 6.1 the system checks connect, which creates an empty SQLite file
    # on a fresh install. migrate, which runs next, does the checks.
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        """Adds --before-migrate."""
        parser.add_argument(
            "--before-migrate",
            action="store_true",
            help=(
                "Only if migrations are pending: keep a copy under its own "
                "name (before-migrate-…), apart from the nightly ones. The "
                "service runs this on every start."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Takes the snapshot and reports where it went."""
        del args
        source = maintenance.database_path()
        if source is None or not source.exists():
            self.stdout.write("Snapshot: no database yet")
            return
        if not options["before_migrate"]:
            path = maintenance.take_snapshot(
                source,
                maintenance.snapshot_dir(),
                timezone.now().date(),
                keep=settings.SNAPSHOT_KEEP,
            )
            self.stdout.write(f"Snapshot: {path}")
            return
        pending = maintenance.pending_migrations()
        if not pending:
            self.stdout.write("Snapshot: not needed, no migrations to apply")
            return
        path = maintenance.snapshot_before_migrate(
            source, maintenance.snapshot_dir(), timezone.now()
        )
        self.stdout.write(
            f"Snapshot before applying {len(pending)} migration(s): {path}"
        )
