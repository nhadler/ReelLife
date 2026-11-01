"""The `reellife` command: manage.py with production settings by default.

Installed by `uv sync` for the bare-metal deployment, e.g.
`reellife migrate` or `reellife nightly`.
"""

import os
import sys

from django.core.management import execute_from_command_line


def main() -> None:
    """Runs a management command."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "reellife.settings.prod")
    execute_from_command_line(sys.argv)
