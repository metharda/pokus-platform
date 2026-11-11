"""The agent's package stands alone: no dependency on another package here."""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

PACKAGES = Path(__file__).resolve().parents[2] / "packages"
AGENT = PACKAGES / "potka-agent"


def test_the_agent_package_declares_no_dependency() -> None:
    project = tomllib.loads((AGENT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["dependencies"] == []


def test_the_agent_code_imports_only_itself_and_the_standard_library() -> None:
    sources = sorted((AGENT / "src").rglob("*.py"))
    assert sources
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, f"{source}: relative import"
                names = [node.module or ""]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                assert top == "potka_agent" or top in sys.stdlib_module_names, (
                    f"{source}: imports {name}"
                )
