"""The database file, and a data directory from before the rename."""

import pathlib

from django.core.exceptions import ImproperlyConfigured
import pytest

from reeltime.settings.base import database_file


def test_the_database_is_reeltime_sqlite3(tmp_path: pathlib.Path) -> None:
    assert database_file(tmp_path) == tmp_path / "reeltime.sqlite3"
    (tmp_path / "reeltime.sqlite3").touch()
    (tmp_path / "reellife.sqlite3").touch()  # renamed already, old left
    assert database_file(tmp_path) == tmp_path / "reeltime.sqlite3"


def test_only_the_old_database_stops_reeltime(tmp_path: pathlib.Path) -> None:
    (tmp_path / "reellife.sqlite3").touch()
    with pytest.raises(ImproperlyConfigured, match="rename it"):
        database_file(tmp_path)
