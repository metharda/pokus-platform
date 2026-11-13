"""The golden images table: ``platform/infra-data/golden-images.json``.

The platform trusts no property of an image. What it needs to know of a
golden image's blueprint (its version, its type, whether it needs nesting,
its packages) comes from this table, generated from
pokus-infrastructure's blueprints (``images/*/image.yaml``) at INFRA_PIN,
committed with the release and compared with a fresh generation by the
``generated`` check.

The table::

    {"infra_pin": "<the commit>",
     "blueprints": {"<name>": {"version": "...",
                               "type": "container" or "vm",
                               "nesting": true or false,
                               "base": {"image": "..."} or {"blueprint": "..."},
                               "packages": [...],
                               "effective_packages": [...]}}}

``packages`` is the blueprint's own list, as its file writes it;
``effective_packages`` adds the packages of every blueprint it is built on,
each once, sorted. A package may carry a version (``name=version``), kept as
written. Every blueprint is listed alike. The one the others are built on
is found from the data (it is built from an image, and exactly one blueprint
is); nothing here knows its name.

Each file is read with PyYAML, as pokus-infrastructure reads it, and
validated against pokus-infrastructure's own schema for it at the pin. The
checkout must be at INFRA_PIN, with its blueprints and their schema
unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from potka_ci.contract import PIN_FILE, read_pin
from potka_ci.repo import GitError, git

IMAGES = Path("images")
BLUEPRINT = "image.yaml"
SCHEMA = Path("tools") / "lockcheck" / "schema" / "image-blueprint.schema.json"
TABLE = Path("platform") / "infra-data" / "golden-images.json"


class TableError(ValueError):
    """The blueprints of a checkout do not make a table.

    Attributes:
        problems: Every reason.
    """

    def __init__(self, problems: list[str]) -> None:
        """Keep the problems.

        Args:
            problems: Every reason.
        """
        super().__init__("; ".join(problems))
        self.problems = problems


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def read_blueprints(infra: Path) -> dict[str, dict[str, Any]]:
    """Every blueprint of the checkout INFRA, validated, by its name.

    Args:
        infra: A pokus-infrastructure checkout.

    Returns:
        Each ``images/<name>/image.yaml`` as data.

    Raises:
        TableError: The schema or a blueprint cannot be read, the schema is
            no JSON Schema, there is no blueprint, a blueprint does not
            match the schema or is not in the directory of its name, or a
            value the table takes holds white space at an end or a
            character that cannot be printed (the schema's patterns, as
            Python reads them, let a final newline pass).
    """
    try:
        schema = json.loads((infra / SCHEMA).read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        validator = jsonschema.Draft202012Validator(schema)
    except (OSError, ValueError) as exc:
        raise TableError([f"{SCHEMA.as_posix()} cannot be read: {exc}"]) from exc
    except jsonschema.SchemaError as exc:
        raise TableError([f"{SCHEMA.as_posix()} is no JSON Schema: {exc.message}"]) from exc
    files = sorted((infra / IMAGES).glob(f"*/{BLUEPRINT}"))
    if not files:
        raise TableError([f"no {IMAGES.as_posix()}/*/{BLUEPRINT} in the checkout"])
    problems: list[str] = []
    blueprints: dict[str, dict[str, Any]] = {}
    for path in files:
        shown = path.relative_to(infra).as_posix()
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            problems.append(f"{shown} cannot be read: {type(exc).__name__}")
            continue
        errors = sorted(validator.iter_errors(data), key=lambda e: list(map(str, e.absolute_path)))
        problems += [
            f"{shown}: {'/'.join(str(p) for p in error.absolute_path) or 'the file'}:"
            f" {error.message}"
            for error in errors
        ]
        if errors:
            continue
        taken = [data["name"], data["version"], *_strings(data["base"]), *data["packages"]]
        if any(text != text.strip() or not text.isprintable() for text in taken):
            problems.append(f"{shown}: a value with white space at an end or an unprintable one")
        elif data["name"] != path.parent.name:
            problems.append(f"{shown}: the blueprint {data['name']} is not in its own directory")
        else:
            blueprints[data["name"]] = data
    if problems:
        raise TableError(problems)
    return blueprints


def table(blueprints: Mapping[str, Mapping[str, Any]], pin: str) -> dict[str, Any]:
    """The table of BLUEPRINTS (the module's docstring has its form).

    Args:
        blueprints: The blueprints by name (`read_blueprints`).
        pin: The commit they are read at.

    Returns:
        The table.

    Raises:
        TableError: Not exactly one blueprint is built from an image, or a
            blueprint is built on one that does not exist or, through
            others, on itself.
    """
    roots = sorted(name for name, blueprint in blueprints.items() if "image" in blueprint["base"])
    if len(roots) != 1:
        raise TableError(
            [
                f"exactly one blueprint is built from an image, not {len(roots)}"
                + (f": {', '.join(roots)}" if roots else "")
            ]
        )
    problems: list[str] = []
    entries: dict[str, Any] = {}
    for name, blueprint in sorted(blueprints.items()):
        chain = [name]
        while "blueprint" in blueprints[chain[-1]]["base"]:
            below = blueprints[chain[-1]]["base"]["blueprint"]
            if below not in blueprints:
                problems.append(f"{chain[-1]} is built on {below}, which is no blueprint")
                break
            if below in chain:
                # The circle, named from its own blueprint: one above it is not built on itself.
                circle = [*chain[chain.index(below) :], below]
                problems.append(f"{below} is built on itself: {' on '.join(circle)}")
                break
            chain.append(below)
        entries[name] = {
            "version": blueprint["version"],
            "type": blueprint["type"],
            "nesting": blueprint["nesting"],
            "base": dict(blueprint["base"]),
            "packages": list(blueprint["packages"]),
            "effective_packages": sorted({p for b in chain for p in blueprints[b]["packages"]}),
        }
    if problems:
        # Each once: every blueprint above a missing or a circular one meets the same problem.
        raise TableError(list(dict.fromkeys(problems)))
    return {"infra_pin": pin, "blueprints": entries}


def render(data: Mapping[str, Any]) -> str:
    """The table as the committed file holds it.

    Args:
        data: The table.

    Returns:
        JSON with sorted keys, indented by two spaces, with a final newline.
    """
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def generate(root: Path, infra: Path) -> str:
    """The table's text from the checkout INFRA, which must be at INFRA_PIN.

    Args:
        root: The working tree (its ``INFRA_PIN``).
        infra: The pokus-infrastructure checkout.

    Returns:
        The text of ``platform/infra-data/golden-images.json``.

    Raises:
        TableError: ``INFRA_PIN`` is not one commit hash, the checkout is not
            at it or has changes under its blueprints or their schema (a
            changed, an untracked or an ignored file), or the blueprints do
            not make a table.
    """
    pin = read_pin(root)
    if pin is None:
        raise TableError([f"{PIN_FILE}: not one full commit hash on one line"])
    try:
        head = git(infra, ["rev-parse", "HEAD"]).strip()
        # Untracked and ignored files too, whatever the checkout's configuration shows.
        changed = git(
            infra,
            [
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--ignored",
                "--",
                IMAGES.as_posix(),
                SCHEMA.as_posix(),
            ],
        )
    except GitError as exc:
        raise TableError([str(exc)]) from exc
    if head != pin:
        raise TableError(
            [f"the pokus-infrastructure checkout is at {head}, not at {PIN_FILE} {pin}"]
        )
    if changed.strip():
        raise TableError(
            ["the pokus-infrastructure checkout has changes under its blueprints or their schema"]
        )
    return render(table(read_blueprints(infra), pin))
