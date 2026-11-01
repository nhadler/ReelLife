"""Marks every test with its Google test size from its directory."""

import pathlib

import pytest

_SIZES = ("small", "medium", "large")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Adds a small/medium/large marker based on tests/<size>/."""
    for item in items:
        parts = pathlib.Path(item.path).parts
        for size in _SIZES:
            if size in parts:
                item.add_marker(getattr(pytest.mark, size))
