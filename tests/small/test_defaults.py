"""Seed data still matches the reference files.

The maintainer's private/reference/ folder isn't published; without it
these tests skip.
"""

import importlib
import json
import pathlib
import re
from typing import Any

import pytest

from cues import columns
from cues import defaults
from cues.models import STATUS_KIND_INFO
from cues.models import StatusKind

_REFERENCE = (
    pathlib.Path(__file__).resolve().parents[2] / "private" / "reference"
)


def _load(name: str) -> Any:
    path = _REFERENCE / name
    if not path.exists():  # a copy without the private folder
        pytest.skip("The private/reference/ folder isn't here.")
    return json.loads(path.read_text(encoding="utf-8"))


def test_statuses_match_the_reference_list() -> None:
    reference = _load("enums.json")["status"]
    assert [(label, color) for label, _, color in defaults.STATUSES] == [
        (label, color) for label, _, color in reference
    ]


def test_status_kinds_agree_with_the_reference_categories() -> None:
    reference = {
        label: category for label, category, _ in _load("enums.json")["status"]
    }
    for label, kind, _ in defaults.STATUSES:
        assert STATUS_KIND_INFO[kind][0] == reference[label], label


def test_every_kind_has_info() -> None:
    assert set(STATUS_KIND_INFO) == set(StatusKind.values)


def test_sub_statuses_match_the_reference() -> None:
    reference = _load("enums.json")["sub_status"]
    assert list(defaults.SUB_STATUSES) == [
        (name, color) for name, _, color in reference
    ]


def test_every_colour_has_a_hex_value() -> None:
    used = {color for *_, color in defaults.STATUSES} | {
        color for _, color in defaults.SUB_STATUSES
    }
    assert used <= set(defaults.TAILWIND)
    assert all(
        re.fullmatch(r"#[0-9a-f]{6}", v) for v in defaults.TAILWIND.values()
    )


@pytest.mark.parametrize(("view", "preset"), defaults.VIEW_PRESETS)
def test_view_columns_come_from_the_presets(view: str, preset: str) -> None:
    labels = _load("preset_columns.json")[preset]
    derived = [
        column
        for label in labels
        if (column := defaults.PRESET_COLUMNS[label]) is not None
    ]
    # Plus ReelTime's own additions, each after its column.
    for column, after in defaults.VIEW_EXTRAS.get(view, ()):
        derived.insert(derived.index(after) + 1, column)
    assert defaults.VIEW_COLUMNS[view] == tuple(derived)


def test_every_preset_column_is_mapped() -> None:
    presets = _load("preset_columns.json")
    labels = {
        label
        for name, cols in presets.items()
        if name in dict(defaults.VIEW_PRESETS).values()
        for label in cols
    }
    assert labels <= set(defaults.PRESET_COLUMNS)


def test_mapped_columns_exist_in_registry() -> None:
    mapped = {c for c in defaults.PRESET_COLUMNS.values() if c is not None}
    assert mapped <= set(columns.BY_ID)


def test_column_ids_are_unique() -> None:
    assert len(columns.BY_ID) == len(columns.COLUMNS)


def test_migration_froze_the_current_global_views() -> None:
    # The newest migration that seeds the views.
    folder = pathlib.Path(defaults.__file__).parent / "migrations"
    seeding = sorted(
        path.stem
        for path in folder.glob("0*.py")
        if "\nVIEWS = [" in path.read_text(encoding="utf-8")
    )
    migration = importlib.import_module(f"cues.migrations.{seeding[-1]}")
    frozen = [
        {
            "name": name,
            "order": order,
            "columns": [{"id": c} for c in columns],
            "sort": [{"id": sort, "dir": "asc"}],
            "filter": {},
        }
        for order, (name, sort, columns) in enumerate(migration.VIEWS)
    ]
    assert frozen == defaults.global_views()


@pytest.mark.parametrize(
    "number", ["1m01", "1M01", "1m04a", "1s01", "101m01", "1m01s", "12m110"]
)
def test_default_pattern_accepts_cue_numbers(number: str) -> None:
    assert re.match(defaults.DEFAULT_CUE_NUMBER_PATTERN, number)


@pytest.mark.parametrize(
    "number", ["", "m01", "1m", "Main Title", "1x01", "1m01 "]
)
def test_default_pattern_rejects_other_text(number: str) -> None:
    assert not re.match(defaults.DEFAULT_CUE_NUMBER_PATTERN, number)


@pytest.mark.parametrize(
    ("key", "statuses"),
    [
        ("orchestration_status", defaults.ORCHESTRATION_STATUSES),
        ("recording_status", defaults.RECORDING_STATUSES),
        ("mix_status", defaults.MIX_STATUSES),
    ],
)
def test_department_statuses_match_the_reference(
    key: str, statuses: tuple[tuple[str, str, str], ...]
) -> None:
    assert [list(s) for s in statuses] == _load("enums.json")[key]
    for _, _, color in statuses:
        assert color in defaults.TAILWIND
