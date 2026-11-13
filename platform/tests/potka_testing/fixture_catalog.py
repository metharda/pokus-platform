"""The fixture catalog, and copies of it that a test changes."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

FIXTURE_CATALOG = Path(__file__).resolve().parents[1] / "fixtures" / "catalog"
"""The fixture catalog: synthetic scenarios the catalog lint passes."""


def copy_fixture(directory: Path) -> Path:
    """Copy the fixture catalog into DIRECTORY (which must not exist) and return it."""
    shutil.copytree(FIXTURE_CATALOG, directory, symlinks=True)
    return directory


def edit_yaml(root: Path, name: str, change: Callable[[Any], object]) -> None:
    """Apply CHANGE to the data of the YAML file NAME below ROOT, and write it back."""
    data = yaml.safe_load((root / name).read_text(encoding="utf-8"))
    change(data)
    (root / name).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
