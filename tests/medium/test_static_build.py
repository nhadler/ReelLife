"""Collected static files, as the image and bare-metal installs build them."""

import json
import pathlib
import re

from django.core.management import call_command
from django.test import override_settings
import pytest

from reeltime.settings.base import MANIFEST_STORAGE

HASHED = re.compile(r"\.[0-9a-f]{12}\.js$")
RELATIVE_IMPORT = re.compile(r"""from\s*["'](\.{1,2}/[^"']+)["']""")


@pytest.fixture(name="collected", scope="module")
def fixture_collected(
    tmp_path_factory: pytest.TempPathFactory,
) -> pathlib.Path:
    root = tmp_path_factory.mktemp("static")
    storages = {"staticfiles": {"BACKEND": MANIFEST_STORAGE}}
    with override_settings(STATIC_ROOT=root, STORAGES=storages):
        call_command("collectstatic", "--noinput", verbosity=0)
    return root


def _served(collected: pathlib.Path) -> list[pathlib.Path]:
    """The scripts pages are sent to (the manifest's), not leftovers."""
    manifest = json.loads((collected / "staticfiles.json").read_text())
    return [
        collected / name
        for name in manifest["paths"].values()
        if name.startswith("cues/") and HASHED.search(name)
    ]


def test_modules_import_each_other_by_hashed_name(
    collected: pathlib.Path,
) -> None:
    scripts = _served(collected)
    assert any(p.name.startswith("grid.") for p in scripts)
    imports = {
        f"{p.relative_to(collected)}: {url}"
        for p in scripts
        for url in RELATIVE_IMPORT.findall(p.read_text(encoding="utf-8"))
    }
    assert imports  # the grid and dialogs import modules
    plain = sorted(i for i in imports if not HASHED.search(i))
    assert not plain, plain


def test_a_page_and_an_import_load_the_same_file(
    collected: pathlib.Path,
) -> None:
    # The page loads keys.js by its hashed name and grid.js imports it:
    # one URL, so the browser runs one copy.
    served = {p.name.split(".")[0]: p for p in _served(collected)}
    grid, keys = served["grid"], served["keys"].name
    assert f"./{keys}" in grid.read_text(encoding="utf-8")
