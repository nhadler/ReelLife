"""Vendored front-end files match their pins."""

import hashlib
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
VENDOR = ROOT / "cues" / "static" / "cues" / "vendor"


def _entries() -> list[list[str]]:
    lines = (VENDOR / "VERSIONS").read_text(encoding="utf-8").splitlines()
    return [line.split() for line in lines if line and not line.startswith("#")]


ENTRIES = _entries()


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e[3])
def test_file_matches_its_checksum(entry: list[str]) -> None:
    _, _, _, name, digest = entry
    data = (VENDOR / name).read_bytes()
    assert hashlib.sha256(data).hexdigest() == digest


def test_versions_match_package_json() -> None:
    pins = json.loads((ROOT / "package.json").read_text())["devDependencies"]
    for package, version, *_ in ENTRIES:
        assert pins[package] == version, package


def test_every_vendored_file_is_listed() -> None:
    listed = {entry[3] for entry in ENTRIES} | {"VERSIONS"}
    assert {p.name for p in VENDOR.iterdir()} == listed
