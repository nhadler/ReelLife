"""manage.py seed_demo: build the demo project."""

from typing import Any

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.core.management.base import CommandParser

from cues.services import demo


class Command(BaseCommand):
    """manage.py seed_demo [--replace]."""

    help = "Create the demo project: 3 reels, 2 turnovers, 40 cues."

    def add_arguments(self, parser: CommandParser) -> None:
        """Adds --replace."""
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Delete the existing demo project first.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Builds the project and prints a summary.

        Raises:
            CommandError: If the demo exists and --replace isn't given.
        """
        del args
        try:
            summary = demo.build_demo(replace=options["replace"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"Created {summary.project}: {summary.pictures} reels, "
            f"{summary.turnovers} turnovers, {summary.cues} cues, "
            f"{summary.versions} versions."
        )
