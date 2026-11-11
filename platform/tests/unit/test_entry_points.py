"""The manifest's commands are the console scripts of the right packages."""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

import yaml

PLATFORM = Path(__file__).resolve().parents[2]


def scripts() -> dict[str, str]:
    """Every console script of the workspace and its ``module:function``."""
    found: dict[str, str] = {}
    for pyproject in sorted((PLATFORM / "packages").glob("*/pyproject.toml")):
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        for name, target in project.get("scripts", {}).items():
            assert name not in found, f"{name} is declared twice"
            found[name] = target
    return found


def manifest() -> dict[str, object]:
    data = yaml.safe_load((PLATFORM / "platform-manifest.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_every_start_command_runs_its_own_service() -> None:
    known = scripts()
    services = manifest()["services"]
    assert isinstance(services, list)
    for service in services:
        command = service["start"]
        assert command[0].startswith(".venv/bin/"), command
        target = known[command[0].removeprefix(".venv/bin/")]
        module, _, function = target.partition(":")
        assert module.endswith(".main") and function == "main", target
        assert importlib.import_module(module).SERVICE == service["name"], target


def test_every_hook_runs_the_hooks_entry_point() -> None:
    known = scripts()
    hooks = manifest()["hooks"]
    assert isinstance(hooks, dict)
    for name, hook in hooks.items():
        command = hook["run"]
        assert command[0] == ".venv/bin/potka-hook", (name, command)
        assert command[1:] == [name], (name, command)
    assert known["potka-hook"] == "potka_orchestrator.hooks:main"
