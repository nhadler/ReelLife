"""Tests for the `reeltime` command."""

import os
import sys
from unittest import mock

from reeltime import cli


def test_defaults_to_production_settings() -> None:
    with (
        mock.patch.dict(os.environ, {}, clear=True),
        mock.patch.object(cli, "execute_from_command_line") as execute,
        mock.patch.object(sys, "argv", ["reeltime", "check"]),
    ):
        cli.main()
        assert os.environ["DJANGO_SETTINGS_MODULE"] == "reeltime.settings.prod"
    execute.assert_called_once_with(["reeltime", "check"])


def test_keeps_explicit_settings() -> None:
    with (
        mock.patch.dict(
            os.environ, {"DJANGO_SETTINGS_MODULE": "x"}, clear=True
        ),
        mock.patch.object(cli, "execute_from_command_line"),
    ):
        cli.main()
        assert os.environ["DJANGO_SETTINGS_MODULE"] == "x"
