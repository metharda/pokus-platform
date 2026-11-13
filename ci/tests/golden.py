"""Synthetic pokus-infrastructure checkouts for the tests of the golden images table."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from conftest import Repo

# The parts of a blueprint's schema the table reads, in the form of the real one.
SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "name", "version", "base", "type", "nesting", "packages"],
    "properties": {
        "schema_version": {"const": 1},
        "name": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,39}[a-z0-9]$"},
        "version": {"type": "string", "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+$"},
        "base": {
            "oneOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["image"],
                    "properties": {"image": {"type": "string"}},
                },
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["blueprint"],
                    "properties": {"blueprint": {"type": "string"}},
                },
            ]
        },
        "type": {"enum": ["container", "vm"]},
        "nesting": {"type": "boolean"},
        "packages": {
            "type": "array",
            "items": {"type": "string", "pattern": "^[a-z0-9][a-z0-9+.-]*(=[A-Za-z0-9.:~+-]+)?$"},
        },
    },
}


def blueprint(name: str, below: str | None, *packages: str, **changes: Any) -> dict[str, Any]:
    """A blueprint built on the blueprint BELOW, or from an image when BELOW is None."""
    return {
        "schema_version": 1,
        "name": name,
        "version": "2.3.4",
        "base": {"image": "some-os-24.04"} if below is None else {"blueprint": below},
        "type": "container",
        "nesting": False,
        "packages": list(packages),
    } | changes


# A root that is not called what any real one is, a chain of two on it, and a sibling.
BLUEPRINTS = [
    blueprint("os-root", None, "curl", "python3", "less"),
    blueprint("middle", "os-root", "nginx=1.24.0-2", "curl"),
    blueprint("leaf", "middle", "app-server", nesting=True, version="10.0.1"),
    blueprint("sibling", "os-root", type="vm"),
]


def infra(path: Path, blueprints: list[dict[str, Any]] | None = None) -> Repo:
    """A committed checkout with the schema and one directory for each blueprint."""
    repo = Repo(path)
    schema = path / "tools" / "lockcheck" / "schema" / "image-blueprint.schema.json"
    schema.parent.mkdir(parents=True)
    schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
    for data in BLUEPRINTS if blueprints is None else blueprints:
        put(path, data)
    repo.git("add", "-A")
    repo.commit("Blueprints")
    return repo


def put(path: Path, data: dict[str, Any], directory: str | None = None) -> Path:
    """Write the blueprint DATA into its directory (or DIRECTORY) below images/."""
    target = path / "images" / (directory or str(data["name"]).strip()) / "image.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return target


def platform(path: Path, pin: str) -> Path:
    """A working tree that holds INFRA_PIN only."""
    path.mkdir(parents=True)
    (path / "INFRA_PIN").write_text(pin + "\n", encoding="utf-8")
    return path
