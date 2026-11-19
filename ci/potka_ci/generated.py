"""The generated files: equal to a fresh generation.

Some files of the release are written by a generator and committed, so a
deployed release needs no generation step:

* the JSON Schemas of the catalog's files (``platform/schemas/catalog/``,
  from potka-core's models; ``make schemas`` writes them);
* the golden images table (``platform/infra-data/golden-images.json``, from
  pokus-infrastructure's blueprints at INFRA_PIN; ``make golden-images``
  writes it; ``potka_ci.golden_images``);
* the Incus API extract (``platform/tests/fixtures/incus-api/api.json``,
  from two files of Incus's repository at the commit ``source.json`` names;
  ``make incus-api`` writes it; ``potka_ci.incus_api``).

The check generates each again and compares it, byte for byte, with the
file of the working tree: in CI, whose tree is the commit, with the
committed file. A file in a generated directory that no generator writes
fails it too. Without a pokus-infrastructure checkout the table cannot be
generated: the schemas are still compared, and the check is skipped, never
passed.

The extract has a check of its own (:func:`extract`), because its source
is not at hand everywhere: two files of Incus's repository, which ``make
incus-sources`` fetches. Without them only that check is skipped, by its
name; :func:`run` compares every other generated file all the same.

potka-core is imported only when the check runs, so the other checks run
without it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from potka_ci import golden_images, incus_api
from potka_ci.outcome import Outcome

SCHEMAS = Path("platform") / "schemas" / "catalog"
# The make target that writes a file (a schema: schemas).
TARGETS = {golden_images.TABLE: "golden-images", incus_api.EXTRACT: "incus-api"}
# What the extract's directory holds besides the extract: its source and what says so.
BESIDE_EXTRACT = {incus_api.EXTRACT, incus_api.SOURCE, incus_api.README}
NO_INFRA = "no pokus-infrastructure checkout (INFRA=<path>)"
NO_INCUS = "no directory with Incus's two files (INCUS=<path>)"
FETCH = (
    "when one of Incus's two files is missing or is not the recorded one: make incus-sources"
    f" INCUS=<path> fetches both at the commit that {incus_api.SOURCE.as_posix()} names"
)
Kind = Literal["schemas", "golden-images", "incus-api"]


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


def write(root: Path, kind: Kind, infra: Path | None = None, incus: Path | None = None) -> Outcome:
    """Write the generated files of one kind.

    Args:
        root: The working tree.
        kind: ``schemas``, ``golden-images`` or ``incus-api``.
        infra: The pokus-infrastructure checkout at the pin (for the table).
        incus: The directory with Incus's two files (for the extract).

    Returns:
        The outcome: what was written, or why it cannot be generated.
    """
    if kind == "schemas":
        return _write(root, catalog_schemas())
    if kind == "incus-api":
        if incus is None:
            return Outcome("skipped", [NO_INCUS])
        try:
            return _write(root, {incus_api.EXTRACT: incus_api.generate(root, incus)})
        except incus_api.ExtractError as exc:
            return Outcome("failed", exc.problems)
    if infra is None:
        return Outcome("skipped", [NO_INFRA])
    try:
        return _write(root, {golden_images.TABLE: golden_images.generate(root, infra)})
    except golden_images.TableError as exc:
        return Outcome("failed", exc.problems)


def _difference(root: Path, path: Path, text: str | None) -> str | None:
    """What is wrong with the file PATH of the working tree, or None.

    Args:
        root: The working tree.
        path: The generated file.
        text: Its fresh generation, or None when that cannot be made here.

    Returns:
        A finding when the file is missing, cannot be read, or is not TEXT.
    """
    target = TARGETS.get(path, "schemas")
    try:
        committed = (root / path).read_bytes()
    except FileNotFoundError:
        return f"{path.as_posix()}: missing (make {target} writes it)"
    except OSError as exc:
        return f"{path.as_posix()}: cannot be read ({type(exc).__name__})"
    if text is not None and committed != text.encode():
        return f"{path.as_posix()}: differs from a fresh generation (make {target})"
    return None


def extract(root: Path, incus: Path | None = None) -> Outcome:
    """Compare the Incus API extract with a fresh one.

    Args:
        root: The working tree.
        incus: The directory with Incus's two files, or None.

    Returns:
        The outcome: failed when the extract is missing, cannot be read,
        cannot be made from the two files or differs from a fresh one
        (when it cannot be made, the last line says how to fetch the two
        files, for the case that one is missing or not the recorded one);
        skipped, with a line that names the extract, when it is there and
        Incus's files are not.
    """
    name = incus_api.EXTRACT.as_posix()
    fresh = None
    if incus is not None:
        try:
            fresh = incus_api.generate(root, incus)
        except incus_api.ExtractError as exc:
            return Outcome("failed", [*(f"{name}: {p}" for p in exc.problems), FETCH])
    finding = _difference(root, incus_api.EXTRACT, fresh)
    if finding is not None:
        return Outcome("failed", [finding])
    if fresh is None:
        return Outcome("skipped", [f"{name} was not compared with a fresh extract: {NO_INCUS}"])
    return Outcome("passed", [f"{name} matches a fresh extract"])


def run(root: Path, infra: Path | None = None) -> Outcome:
    """Compare the schemas and the table with a fresh generation.

    Args:
        root: The working tree.
        infra: The pokus-infrastructure checkout at the pin, or None.

    Returns:
        The outcome: failed when a file differs from its fresh generation,
        is missing, cannot be read or generated, or lies in a generated
        directory (the extract's among them) without a generator; skipped
        when nothing failed and the table was not compared for want of a
        checkout. The extract itself is :func:`extract`'s to compare.
    """
    expected = catalog_schemas()
    findings: list[str] = []
    if infra is not None:
        try:
            expected[golden_images.TABLE] = golden_images.generate(root, infra)
        except golden_images.TableError as exc:
            findings += [f"{golden_images.TABLE.as_posix()}: {p}" for p in exc.problems]
    for path, text in expected.items():
        finding = _difference(root, path, text)
        if finding is not None:
            findings.append(finding)
    for directory, known in (
        (SCHEMAS, set(expected)),
        (golden_images.TABLE.parent, {golden_images.TABLE}),
        (incus_api.DIRECTORY, BESIDE_EXTRACT),
    ):
        present = sorted((root / directory).iterdir()) if (root / directory).is_dir() else []
        findings += [
            f"{entry.relative_to(root).as_posix()}: no generator writes it: remove it"
            for entry in present
            if entry.relative_to(root) not in known
        ]
    if findings:
        return Outcome("failed", findings)
    matched = f"{len(expected)} generated file(s) match a fresh generation"
    if infra is None:
        return Outcome(
            "skipped",
            [f"{matched}; {golden_images.TABLE.as_posix()} was not compared: {NO_INFRA}"],
        )
    return Outcome("passed", [matched])
