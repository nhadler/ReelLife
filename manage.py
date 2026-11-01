#!/usr/bin/env python
"""Django's command-line utility. Defaults to development settings."""

import os
import sys

from django.core.management import execute_from_command_line


def main() -> None:
    """Runs a management command."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "reellife.settings.dev")
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
