"""The generated files: equal to a fresh generation.

Some files of the release are written by a generator and committed, so a
deployed release needs no generation step:

* the JSON Schemas of the catalog's files (``platform/schemas/catalog/``,
  from potka-core's models; ``make schemas`` writes them);
* the golden images table (``platform/infra-data/golden-images.json``, from
  pokus-infrastructure's blueprints at INFRA_PIN; ``make golden-images``
  writes it; ``potka_ci.golden_images``).

The check generates each again and compares it, byte for byte, with the
file of the working tree: in CI, whose tree is the commit, with the
committed file. A file in a generated directory that no generator writes
fails it too. Without a pokus-infrastructure checkout the table cannot be
generated: the schemas are still compared, and the check is skipped, never
passed.

potka-core is imported only when the check runs, so the other checks run
without it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from potka_ci import golden_images
from potka_ci.outcome import Outcome

SCHEMAS = Path("platform") / "schemas" / "catalog"
Kind = Literal["schemas", "golden-images"]


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


def _write(root: Path, files: dict[Path, str]) -> Outcome:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")
    return Outcome("passed", [f"wrote {path.as_posix()}" for path in files])


def write(root: Path, kind: Kind, infra: Path | None = None) -> Outcome:
    """Write the generated files of one kind.

    Args:
        root: The working tree.
        kind: ``schemas`` or ``golden-images``.
        infra: The pokus-infrastructure checkout at the pin (for the table).

    Returns:
        The outcome: what was written, or why the table cannot be generated.
    """
    if kind == "schemas":
        return _write(root, catalog_schemas())
    if infra is None:
        return Outcome("skipped", ["no pokus-infrastructure checkout (INFRA=<path>)"])
    try:
        return _write(root, {golden_images.TABLE: golden_images.generate(root, infra)})
    except golden_images.TableError as exc:
        return Outcome("failed", exc.problems)


def run(root: Path, infra: Path | None = None) -> Outcome:
    """Compare every generated file with a fresh generation.

    Args:
        root: The working tree.
        infra: The pokus-infrastructure checkout at the pin, or None.

    Returns:
        The outcome: failed when a file differs from its fresh generation,
        is missing, cannot be read or generated, or lies in a generated
        directory without a generator; skipped when nothing failed and the
        table was not compared for want of a checkout.
    """
    expected = catalog_schemas()
    findings: list[str] = []
    if infra is not None:
        try:
            expected[golden_images.TABLE] = golden_images.generate(root, infra)
        except golden_images.TableError as exc:
            findings += [f"{golden_images.TABLE.as_posix()}: {p}" for p in exc.problems]
    for path, text in expected.items():
        target = "schemas" if path.parent == SCHEMAS else "golden-images"
        try:
            committed = (root / path).read_bytes()
        except FileNotFoundError:
            findings.append(f"{path.as_posix()}: missing (make {target} writes it)")
            continue
        except OSError as exc:
            findings.append(f"{path.as_posix()}: cannot be read ({type(exc).__name__})")
            continue
        if committed != text.encode():
            findings.append(f"{path.as_posix()}: differs from a fresh generation (make {target})")
    for directory, known in (
        (SCHEMAS, set(expected)),
        (golden_images.TABLE.parent, {golden_images.TABLE}),
    ):
        present = sorted((root / directory).iterdir()) if (root / directory).is_dir() else []
        findings += [
            f"{entry.relative_to(root).as_posix()}: no generator writes it: remove it"
            for entry in present
            if entry.relative_to(root) not in known
        ]
    if findings:
        return Outcome("failed", findings)
    if infra is None:
        return Outcome(
            "skipped",
            [
                f"{len(expected)} generated file(s) match a fresh generation;"
                f" {golden_images.TABLE.as_posix()} was not compared:"
                " no pokus-infrastructure checkout (INFRA=<path>)"
            ],
        )
    return Outcome("passed", [f"{len(expected)} generated file(s) match a fresh generation"])
