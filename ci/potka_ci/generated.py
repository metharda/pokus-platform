"""The generated files: equal to a fresh generation.

Some files of the release are written by a generator and committed, so a
deployed release needs no generation step: the JSON Schemas of the
catalog's files (``platform/schemas/catalog/``, from potka-core's models).
The check generates each again and compares it, byte for byte, with the
file of the working tree: in CI, whose tree is the commit, with the
committed file. A file in a generated directory that no generator writes
fails it too. ``write`` writes the files instead (``make schemas``).

potka-core is imported only when the check runs, so the other checks run
without it.
"""

from __future__ import annotations

import json
from pathlib import Path

from potka_ci.outcome import Outcome

SCHEMAS = Path("platform") / "schemas" / "catalog"


def catalog_schemas() -> dict[Path, str]:
    """The catalog formats' JSON Schemas, as the committed files hold them.

    Returns:
        Each file's path (relative to the repository) and its text: JSON
        with sorted keys, indented by two spaces, with a final newline.
    """
    from potka_core.catalog.schemas import schemas

    return {
        SCHEMAS / name: json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        for name, schema in schemas().items()
    }


def run(root: Path, write: bool = False) -> Outcome:
    """Compare every generated file with a fresh generation, or write them.

    Args:
        root: The working tree.
        write: Write the files instead of comparing them.

    Returns:
        The outcome: failed when a file differs from its fresh generation,
        is missing, cannot be read, or lies in a generated directory without
        a generator.
    """
    expected = catalog_schemas()
    if write:
        for path, text in expected.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(text, encoding="utf-8")
        return Outcome("passed", [f"wrote {path.as_posix()}" for path in expected])
    findings = []
    for path, text in expected.items():
        try:
            committed = (root / path).read_bytes()
        except FileNotFoundError:
            findings.append(f"{path.as_posix()}: missing (make schemas writes it)")
            continue
        except OSError as exc:
            findings.append(f"{path.as_posix()}: cannot be read ({type(exc).__name__})")
            continue
        if committed != text.encode():
            findings.append(f"{path.as_posix()}: differs from a fresh generation (make schemas)")
    directory = root / SCHEMAS
    present = sorted(directory.iterdir()) if directory.is_dir() else []
    findings += [
        f"{entry.relative_to(root).as_posix()}: no generator writes it: remove it"
        for entry in present
        if entry.relative_to(root) not in expected
    ]
    if findings:
        return Outcome("failed", findings)
    return Outcome("passed", [f"{len(expected)} generated file(s) match a fresh generation"])
