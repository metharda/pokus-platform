"""The gateway, the executor and the agent runner import no catalog code.

The import graph is read from the sources: every import statement of every
module the three entry points reach inside this repository's packages,
wherever in a module it stands. Statements are all the graph sees, so the
same modules may not import by other means: no ``importlib``, ``__import__``,
``exec``, ``eval`` or ``import *``. A last test imports the entry points and
looks at what was loaded.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGES = Path(__file__).resolve().parents[2] / "packages"
ENTRY_POINTS = ("potka_gateway.main", "potka_executor.main", "potka_agent.main")
CATALOG = "potka_core.catalog"


def modules(packages: Path) -> dict[str, Path]:
    """Every module of the packages' sources, by its dotted name."""
    found: dict[str, Path] = {}
    for source in sorted(packages.glob("*/src/**/*.py")):
        relative = source.relative_to(next(p for p in source.parents if p.name == "src"))
        parts = relative.with_suffix("").parts
        name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        assert name not in found, f"{name} has two sources"
        found[name] = source
    return found


def imported(name: str, source: Path, known: dict[str, Path]) -> set[str]:
    """The known modules NAME imports, with the packages above each."""
    package = name if source.name == "__init__.py" else name.rpartition(".")[0]
    targets: set[str] = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"), filename=str(source))):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                above = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*above, base] if base else above)
            targets.add(base)
            targets.update(f"{base}.{alias.name}" for alias in node.names)
    with_parents = {
        ".".join(target.split(".")[:end])
        for target in targets
        for end in range(1, len(target.split(".")) + 1)
    }
    return with_parents & set(known)


def reachable(entry: str, known: dict[str, Path]) -> set[str]:
    """Every known module the entry point imports, directly or through others."""
    seen: set[str] = set()
    queue = [entry, *(".".join(entry.split(".")[:end]) for end in range(1, entry.count(".") + 1))]
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        queue.extend(imported(name, known[name], known))
    return seen


def other_means(source: Path) -> list[str]:
    """Where a module could import without an import statement the graph follows."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"), filename=str(source))):
        if isinstance(node, ast.Name) and node.id in ("__import__", "exec", "eval", "importlib"):
            found.append((node.lineno, node.id))
        elif isinstance(node, ast.Import | ast.ImportFrom):
            module = node.module if isinstance(node, ast.ImportFrom) else None
            named = [name for name in (module, *(alias.name for alias in node.names)) if name]
            if "*" in named or any(n == "importlib" or n.startswith("importlib.") for n in named):
                found.append((node.lineno, f"import of {named[0]}"))
    return [f"{line}: {what}" for line, what in sorted(found)]


def catalog_modules(names: set[str]) -> list[str]:
    return sorted(n for n in names if n == CATALOG or n.startswith(CATALOG + "."))


def test_the_catalog_code_is_where_the_tests_look_for_it() -> None:
    known = modules(PACKAGES)
    assert CATALOG in known
    assert len(catalog_modules(set(known))) > 4
    for entry in ENTRY_POINTS:
        assert entry in known


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_no_module_an_entry_point_reaches_imports_the_catalog(entry: str) -> None:
    known = modules(PACKAGES)
    reached = reachable(entry, known)
    assert entry in reached
    assert catalog_modules(reached) == []
    if entry != "potka_agent.main":
        assert "potka_core.server" in reached


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_no_module_an_entry_point_reaches_imports_by_other_means(entry: str) -> None:
    known = modules(PACKAGES)
    for name in sorted(reachable(entry, known)):
        assert other_means(known[name]) == [], name


def test_other_means_of_importing_are_found(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text(
        "import os\n"
        "import importlib\n"
        "from importlib import import_module\n"
        "import importlib.util as u\n"
        "from potka_core import *\n"
        "def late():\n"
        "    __import__('potka_core.catalog')\n"
        "    exec('import potka_core.catalog')\n"
        "    return eval('1')\n",
        encoding="utf-8",
    )
    assert other_means(source) == [
        "2: import of importlib",
        "3: import of importlib",
        "4: import of importlib.util",
        "5: import of potka_core",
        "7: __import__",
        "8: exec",
        "9: eval",
    ]


def test_the_services_that_read_the_catalog_would_be_found(tmp_path: Path) -> None:
    """The graph follows every import statement, at any depth of a module."""
    sources = {
        "potka-core/src/potka_core/__init__.py": "",
        "potka-core/src/potka_core/server.py": "from potka_core import helper\n",
        "potka-core/src/potka_core/helper.py": "",
        "potka-core/src/potka_core/catalog/__init__.py": "",
        "potka-core/src/potka_core/catalog/scenario.py": "",
        "potka-core/src/potka_core/lazy.py": (
            "def late():\n    import potka_core.catalog.scenario\n"
        ),
        "potka-core/src/potka_core/renamed.py": "from potka_core import catalog as c\n",
        "potka-core/src/potka_core/relative.py": "from .catalog import scenario\n",
        "potka-core/src/potka_core/sub/__init__.py": "",
        "potka-core/src/potka_core/sub/up.py": "from ..catalog.scenario import x\n",
        "potka-core/src/potka_core/sub/dots.py": "from .. import catalog\n",
        "potka-core/src/potka_core/guarded.py": (
            "try:\n    import os\nexcept ImportError:\n"
            "    from potka_core.catalog import scenario\n"
        ),
        "potka-a/src/potka_a/__init__.py": "",
        "potka-a/src/potka_a/main.py": "from potka_core.server import x\nimport json\n",
    }
    for form in ("lazy", "renamed", "relative", "sub.up", "sub.dots", "guarded"):
        sources[f"potka-a/src/potka_a/via_{form.replace('.', '_')}.py"] = (
            f"import potka_core.{form}\n"
        )
    for name, text in sources.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    known = modules(tmp_path)
    assert reachable("potka_a.main", known) == {
        "potka_a",
        "potka_a.main",
        "potka_core",
        "potka_core.server",
        "potka_core.helper",
    }
    for form in ("lazy", "renamed", "relative", "sub_up", "sub_dots", "guarded"):
        reached = reachable(f"potka_a.via_{form}", known)
        assert CATALOG in catalog_modules(reached), form
    assert catalog_modules(reachable("potka_a.via_lazy", known)) == [
        "potka_core.catalog",
        "potka_core.catalog.scenario",
    ]


def test_importing_the_entry_points_loads_no_catalog_module() -> None:
    script = (
        "import importlib, sys\n"
        f"for name in {ENTRY_POINTS!r}:\n"
        "    importlib.import_module(name)\n"
        "assert 'potka_core.server' in sys.modules\n"
        "print(sorted(n for n in sys.modules if n.startswith('potka_core.catalog')))\n"
        "import potka_core.catalog.scenario\n"
        "print(sorted(n for n in sys.modules if n.startswith('potka_core.catalog'))[:1])\n"
    )
    done = subprocess.run(  # noqa: S603 (this interpreter, a fixed script)
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert done.stdout.splitlines() == ["[]", "['potka_core.catalog']"]
