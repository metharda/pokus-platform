"""The generated files: equal to a fresh generation.

Some files of the release are written by a generator and committed, so a
deployed release needs no generation step:

* the JSON Schemas of the catalog's files (``platform/schemas/catalog/``,
  from potka-core's models) and of the tool registry's files
  (``platform/schemas/tools/``, from potka-tools' models); ``make schemas``
  writes them;
* what the tool registry (``platform/tools/registry/``) is compiled to: the
  tools as a model is given them (``platform/schemas/tools/model-tools.json``),
  what the policy engine is told of the tools and of the registry
  (``platform/policy/tools/data.json``, ``platform/policy/registry/data.json``)
  and the page that lists the tools (``platform/docs/tools.md``); ``make
  tools`` writes them;
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

potka-core and potka-tools are imported only when the check runs, so the
other checks run without them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from potka_ci import golden_images, incus_api
from potka_ci.outcome import Outcome

SCHEMAS = Path("platform") / "schemas" / "catalog"
TOOL_SCHEMAS = Path("platform") / "schemas" / "tools"
REGISTRY = Path("platform") / "tools" / "registry"
MODEL_TOOLS = TOOL_SCHEMAS / "model-tools.json"
POLICY_TOOLS = Path("platform") / "policy" / "tools" / "data.json"
POLICY_REGISTRY = Path("platform") / "policy" / "registry" / "data.json"
TOOLS_PAGE = Path("platform") / "docs" / "tools.md"
# What the tool registry is compiled to.
COMPILED = (MODEL_TOOLS, POLICY_TOOLS, POLICY_REGISTRY, TOOLS_PAGE)
# The make target that writes a file (a schema: schemas).
TARGETS = {
    golden_images.TABLE: "golden-images",
    incus_api.EXTRACT: "incus-api",
    **dict.fromkeys(COMPILED, "tools"),
}
# What the extract's directory holds besides the extract: its source and what says so.
BESIDE_EXTRACT = {incus_api.EXTRACT, incus_api.SOURCE, incus_api.README}
NO_INFRA = "no pokus-infrastructure checkout (INFRA=<path>)"
NO_INCUS = "no directory with Incus's two files (INCUS=<path>)"
FETCH = (
    "when one of Incus's two files is missing or is not the recorded one: make incus-sources"
    f" INCUS=<path> fetches both at the commit that {incus_api.SOURCE.as_posix()} names"
)
Kind = Literal["schemas", "tools", "golden-images", "incus-api"]


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


def tool_schemas() -> dict[Path, str]:
    """The tool registry's JSON Schema, as the committed file holds it.

    Returns:
        The file's path (relative to the repository) and its text, in the
        form of the catalog's schemas.
    """
    from potka_tools.registry import format_schema

    text = json.dumps(format_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return {TOOL_SCHEMAS / "registry.schema.json": text}


def _json(content: object) -> str:
    return json.dumps(content, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def tool_files(root: Path) -> dict[Path, str]:
    """What the tool registry of the working tree ROOT is compiled to.

    Returns:
        Each file's path (relative to the repository) and its text: the
        three JSON files in the form of the schemas, and the page.

    Raises:
        ValueError: The registry cannot be loaded (potka-tools'
            ``RegistryError``, which carries every problem).
    """
    from potka_tools import compile as compiled
    from potka_tools.registry import load_registry

    registry = load_registry(root / REGISTRY)
    return {
        MODEL_TOOLS: _json(compiled.model_tools(registry)),
        POLICY_TOOLS: _json(compiled.policy_tools(registry)),
        POLICY_REGISTRY: _json(compiled.policy_registry(registry)),
        TOOLS_PAGE: compiled.documentation(registry),
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
        kind: ``schemas``, ``tools``, ``golden-images`` or ``incus-api``.
        infra: The pokus-infrastructure checkout at the pin (for the table).
        incus: The directory with Incus's two files (for the extract).

    Returns:
        The outcome: what was written, or why it cannot be generated.
    """
    if kind == "schemas":
        return _write(root, {**catalog_schemas(), **tool_schemas()})
    if kind == "tools":
        try:
            return _write(root, tool_files(root))
        except ValueError as exc:
            return Outcome("failed", [f"{REGISTRY.as_posix()}: {exc}"])
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
    """Compare the schemas, the registry's compiled files and the table with a fresh generation.

    Args:
        root: The working tree.
        infra: The pokus-infrastructure checkout at the pin, or None.

    Returns:
        The outcome: failed when a file differs from its fresh generation,
        is missing, cannot be read or generated (the registry's compiled
        files when the registry cannot be loaded), or lies in a generated
        directory (the extract's among them) without a generator; skipped
        when nothing failed and the table was not compared for want of a
        checkout. The extract itself is :func:`extract`'s to compare.
    """
    schemas = {**catalog_schemas(), **tool_schemas()}
    expected: dict[Path, str | None] = dict(schemas)
    findings: list[str] = []
    try:
        expected.update(tool_files(root))
    except ValueError as exc:
        findings.append(f"{REGISTRY.as_posix()}: {exc}")
        # Without a registry its files have no fresh generation: each must still be there.
        expected.update(dict.fromkeys(COMPILED))
    if infra is not None:
        try:
            expected[golden_images.TABLE] = golden_images.generate(root, infra)
        except golden_images.TableError as exc:
            findings += [f"{golden_images.TABLE.as_posix()}: {p}" for p in exc.problems]
    for path, text in expected.items():
        finding = _difference(root, path, text)
        if finding is not None:
            findings.append(finding)
    written = {*schemas, *COMPILED}
    for directory, known in (
        (SCHEMAS, written),
        (TOOL_SCHEMAS, written),
        (POLICY_TOOLS.parent, written),
        (POLICY_REGISTRY.parent, written),
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
