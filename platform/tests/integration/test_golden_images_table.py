"""The committed golden images table is pokus-infrastructure's blueprints at INFRA_PIN."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

PLATFORM = Path(__file__).resolve().parents[2]
TABLE = PLATFORM / "infra-data" / "golden-images.json"


def table() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(TABLE.read_text(encoding="utf-8"))
    return data


def test_the_table_is_of_the_pinned_commit() -> None:
    pin = (PLATFORM.parent / "INFRA_PIN").read_text(encoding="utf-8").strip()
    assert table()["infra_pin"] == pin
    assert sorted(table()) == ["blueprints", "infra_pin"]


def test_every_blueprint_of_the_checkout_is_in_the_table_as_its_file_has_it(
    infra_checkout: Path,
) -> None:
    files = sorted((infra_checkout / "images").glob("*/image.yaml"))
    blueprints = {path.parent.name: yaml.safe_load(path.read_text("utf-8")) for path in files}
    entries = table()["blueprints"]
    assert sorted(entries) == sorted(blueprints)
    assert len(entries) >= 2
    for name, blueprint in blueprints.items():
        entry = entries[name]
        assert sorted(entry) == [
            "base",
            "effective_packages",
            "nesting",
            "packages",
            "type",
            "version",
        ]
        assert blueprint["name"] == name
        for key in ("version", "type", "nesting", "base", "packages"):
            assert entry[key] == blueprint[key], (name, key)
        assert type(entry["nesting"]) is bool
        assert entry["type"] in ("container", "vm")
        # The packages of the blueprint and of every blueprint below it, from the files.
        packages: set[str] = set()
        below: Any = blueprint
        for _ in blueprints:
            packages.update(below["packages"])
            if "image" in below["base"]:
                break
            below = blueprints[below["base"]["blueprint"]]
        else:
            raise AssertionError(f"{name} is built on no image")
        assert entry["effective_packages"] == sorted(packages), name


def test_one_blueprint_is_built_from_an_image_and_the_others_carry_its_packages() -> None:
    entries = table()["blueprints"]
    roots = [name for name, entry in entries.items() if "image" in entry["base"]]
    assert len(roots) == 1
    root = entries[roots[0]]
    assert root["effective_packages"] == sorted(root["packages"])
    for name, entry in entries.items():
        assert entry["effective_packages"] == sorted(set(entry["effective_packages"])), name
        assert set(entry["packages"]) <= set(entry["effective_packages"]), name
        assert set(root["packages"]) <= set(entry["effective_packages"]), name
        if name != roots[0]:
            assert entry["base"] == {"blueprint": roots[0]} or entry["base"]["blueprint"] in entries
