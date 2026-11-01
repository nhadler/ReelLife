"""manage.py nightly: snapshot and housekeeping (systemd timer runs it)."""

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from reellife import maintenance


class Command(BaseCommand):
    """Runs the nightly maintenance once."""

    help = (
        "Snapshot the database, write a backup, and purge old audit events "
        "and sessions."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        """Runs it and prints a summary."""
        del args, options
        result = maintenance.nightly(timezone.now())
        self.stdout.write(
            f"Snapshot: {result.snapshot or 'no database yet'}; backup: "
            f"{result.backup}; purged "
            f"{result.audit_events_purged} audit events and "
            f"{result.sessions_purged} sessions."
        )
