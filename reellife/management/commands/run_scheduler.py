"""manage.py run_scheduler: the Compose scheduler service's loop."""

import signal
import threading
from types import FrameType
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from reellife import maintenance


class Command(BaseCommand):
    """Runs nightly maintenance every day until stopped."""

    help = "Run nightly maintenance at NIGHTLY_HOUR_UTC until SIGTERM."

    def handle(self, *args: Any, **options: Any) -> None:
        """Loops until SIGTERM or SIGINT (docker stop)."""
        del args, options
        stop = threading.Event()

        def request_stop(signum: int, frame: FrameType | None) -> None:
            del signum, frame
            stop.set()

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        self.stdout.write(
            f"Scheduler running; nightly at {settings.NIGHTLY_HOUR_UTC}:00 UTC."
        )
        maintenance.run_forever(
            clock=timezone.now,
            wait=stop.wait,
            job=maintenance.nightly,
            hour=settings.NIGHTLY_HOUR_UTC,
        )
        self.stdout.write("Scheduler stopped.")
