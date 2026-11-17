"""Reading a catalog checkout: its layout, its YAML and its files' models.

The layout, below the catalog's root::

    taxonomy.yaml
    blueprints/<name>.yaml
    blueprints/<derived name>/overlay/
    scenarios/<ID>/<version>/scenario.yaml
    scenarios/<ID>/<version>/ground_truth.yaml

A blueprint description's file is named after its ``name``, a derived
image's directory after the derived image, a scenario's directories after
its ``id`` and its ``version`` (a positive integer without leading zeros).
``blueprints/`` and ``scenarios/`` hold nothing else; a missing one is an
empty one. Other entries of the root (a README, the repository's own
files) are not read. Below the root no symbolic link is followed: a link
where a file or a directory is expected is refused. So is an entry of
``blueprints/`` or ``scenarios/`` whose name is not printable UTF-8.

The YAML is a strict subset: one document of mappings with string keys (no
key twice), sequences and scalars; no alias, no tag. A plain scalar is
``null`` or ``~`` (null), ``true`` or ``false``, or a number as JSON writes
it; everything else is a string, so ``yes``, ``on``, ``0x10``, ``1_000``
and a date are strings. The data is then exactly JSON's.

An error names the file and the place; of a file's content it shows at
most a key or a name the models' messages show (`potka_core.catalog.base`).
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import JsonValue, ValidationError

from potka_core.catalog.base import Model
from potka_core.catalog.blueprint import Blueprint
from potka_core.catalog.ground_truth import GroundTruth
from potka_core.catalog.overlay import Overlay, OverlayError, read_overlay
from potka_core.catalog.scenario import Scenario
from potka_core.catalog.taxonomy import Taxonomy

TAXONOMY = "taxonomy.yaml"
BLUEPRINTS = "blueprints"
SCENARIOS = "scenarios"
OVERLAY = "overlay"
SCENARIO = "scenario.yaml"
GROUND_TRUTH = "ground_truth.yaml"
_VERSION = re.compile(r"[1-9][0-9]*")
# The form of a derived image's name (`potka_core.catalog.base.Name`).
_DERIVED = re.compile(r"(?=.{1,63}\Z)[a-z][a-z0-9]*(-[a-z0-9]+)*")


class _Loader(yaml.SafeLoader):
    """YAML's safe loader, narrowed to the subset of the module's docstring."""

    # Only these plain scalars are not strings.
    yaml_implicit_resolvers = {}  # noqa: RUF012 (PyYAML's own class attribute, replaced)

    def compose_node(self, parent: Any, index: Any) -> Any:
        event = self.peek_event()  # type: ignore[no-untyped-call]
        if isinstance(event, yaml.AliasEvent):
            raise _refused("an alias", event)
        if getattr(event, "tag", None) is not None:
            raise _refused("a tag", event)
        return super().compose_node(parent, index)

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            if not isinstance(key, str):
                raise _refused("a key that is not a string", key_node)
            if key in seen:
                raise _refused("a key given twice", key_node)
            seen.add(key)
        return super().construct_mapping(node, deep)


class _Refused(yaml.MarkedYAMLError):
    """YAML outside the subset, refused here: the problem's text is this module's own."""


def _refused(problem: str, where: Any) -> _Refused:
    return _Refused(problem=problem, problem_mark=where.start_mark)


_NUMBER = r"-?(?:0|[1-9][0-9]*)"
_Loader.add_implicit_resolver("tag:yaml.org,2002:null", re.compile(r"^(?:null|~|)$"), None)
_Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$"), None)
_Loader.add_implicit_resolver("tag:yaml.org,2002:int", re.compile(rf"^{_NUMBER}$"), None)
_Loader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(rf"^{_NUMBER}(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?$"),
    None,
)


class CatalogError(ValueError):
    """A catalog checkout cannot be loaded.

    Attributes:
        problems: Every problem found, each with the path it is about
            (relative to the catalog's root).
    """

    def __init__(self, problems: list[tuple[str, str]]) -> None:
        """Keep the problems.

        Args:
            problems: Each path and what is wrong there.
        """
        super().__init__("; ".join(f"{path}: {reason}" for path, reason in problems))
        self.problems = tuple(problems)


@dataclass(frozen=True)
class ScenarioVersion:
    """One version of a scenario, with its ground truth.

    Attributes:
        path: Its directory, relative to the catalog's root.
        scenario: ``scenario.yaml``.
        ground_truth: ``ground_truth.yaml``.
    """

    path: str
    scenario: Scenario
    ground_truth: GroundTruth


@dataclass(frozen=True)
class Catalog:
    """A catalog checkout whose every file has its form.

    Attributes:
        root: The catalog's root.
        taxonomy: ``taxonomy.yaml``.
        blueprints: The blueprint descriptions by name.
        overlays: The overlays by their derived image's name.
        scenarios: Every scenario version, by ID and then version.
    """

    root: Path
    taxonomy: Taxonomy
    blueprints: dict[str, Blueprint]
    overlays: dict[str, Overlay]
    scenarios: tuple[ScenarioVersion, ...]


def parse_yaml(text: str) -> JsonValue:
    """The JSON data of the YAML document TEXT (the module's docstring has the subset).

    Args:
        text: One YAML document.

    Returns:
        Its data: dictionaries with string keys, lists, strings, numbers,
        booleans and None.

    Raises:
        ValueError: TEXT is not YAML of the subset, is nested too deeply or
            holds a number too large for a float. The message gives the
            reason in this module's own words, with the line and column
            where YAML names them, and never the text.
    """
    try:
        data: JsonValue = yaml.load(text, Loader=_Loader)  # noqa: S506 (a safe loader, narrowed)
        json.dumps(data, allow_nan=False)
    except yaml.MarkedYAMLError as exc:
        # PyYAML's own messages can quote the document, so only this module's are shown.
        if isinstance(exc, _Refused):
            problem = str(exc.problem)
        elif exc.context == "expected a single document in the stream":
            problem = "more than one document"
        else:
            problem = "a syntax error"
        mark = exc.problem_mark
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        raise ValueError(f"not YAML of the catalog's subset: {problem}{where}") from None
    except yaml.YAMLError:
        raise ValueError("not YAML of the catalog's subset: a syntax error") from None
    except RecursionError:
        raise ValueError("not YAML of the catalog's subset: nested too deeply") from None
    except ValueError:
        raise ValueError("not YAML of the catalog's subset: a number too large") from None
    return data


def _kind(path: Path) -> str:
    """What PATH is, without following a link: ``file``, ``directory``, ``link``, ..."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return "missing"
    if stat.S_ISLNK(mode):
        return "link"
    if stat.S_ISDIR(mode):
        return "directory"
    return "file" if stat.S_ISREG(mode) else "special"


class _Reader:
    """Loads the files of one checkout and collects every problem."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.problems: list[tuple[str, str]] = []

    def problem(self, path: str, reason: str) -> None:
        self.problems.append((path, reason))

    def kind(self, path: str) -> str | None:
        """What PATH is (`_kind`), or None after recording that it cannot be examined."""
        try:
            return _kind(self.root / path)
        except OSError as exc:
            self.problem(path, f"cannot be read ({type(exc).__name__})")
            return None

    def model[M: Model](self, path: str, model: type[M]) -> M | None:
        """The file PATH as MODEL, or None after recording why not."""
        kind = self.kind(path)
        if kind is None:
            return None
        if kind != "file":
            self.problem(path, "missing" if kind == "missing" else f"a {kind}, not a file")
            return None
        try:
            data = parse_yaml((self.root / path).read_text(encoding="utf-8"))
            return model.model_validate_json(json.dumps(data), strict=True)
        except UnicodeDecodeError:
            self.problem(path, "not UTF-8")
        except OSError as exc:
            self.problem(path, f"cannot be read ({type(exc).__name__})")
        except ValidationError as exc:
            for error in exc.errors(include_url=False, include_input=False, include_context=False):
                where = ".".join(str(part) for part in error["loc"])
                self.problem(path, f"{where}: {error['msg']}" if where else error["msg"])
        except ValueError as exc:
            self.problem(path, str(exc))
        return None

    def directory(self, path: str) -> list[str] | None:
        """The entries of the directory PATH; empty when missing, None when not a directory.

        An entry whose name is not printable UTF-8 is recorded as a problem
        and left out, so every path of a problem can be printed on one line.
        """
        kind = self.kind(path)
        if kind == "missing":
            return []
        if kind != "directory":
            if kind is not None:
                self.problem(path, f"a {kind}, not a directory")
            return None
        try:
            entries = sorted(os.listdir(self.root / path))
        except OSError as exc:
            self.problem(path, f"cannot be read ({type(exc).__name__})")
            return None
        for entry in entries:
            if not entry.isprintable():
                # A byte that is not UTF-8 reaches Python as a lone surrogate, not printable.
                shown = "".join(char if char.isprintable() else "?" for char in entry)
                self.problem(f"{path}/{shown}", "a name that is not printable UTF-8")
        return [entry for entry in entries if entry.isprintable()]

    def blueprints(self) -> tuple[dict[str, Blueprint], dict[str, Overlay]]:
        blueprints: dict[str, Blueprint] = {}
        overlays: dict[str, Overlay] = {}
        for entry in self.directory(BLUEPRINTS) or []:
            path = f"{BLUEPRINTS}/{entry}"
            kind = self.kind(path)
            if kind is None:
                continue
            if kind == "directory":
                entries = self.directory(path)
                if entries is None:
                    continue
                if not _DERIVED.fullmatch(entry):
                    self.problem(path, "not the name of a derived image")
                    continue
                for extra in sorted(set(entries) - {OVERLAY}):
                    self.problem(
                        f"{path}/{extra}", f"a derived image's directory holds {OVERLAY}/ only"
                    )
                if OVERLAY not in entries:
                    self.problem(path, f"a derived image's directory without {OVERLAY}/")
                if entries != [OVERLAY]:
                    continue
                try:
                    overlays[entry] = read_overlay(self.root / path / OVERLAY)
                except OverlayError as exc:
                    for inner, reason in exc.problems:
                        self.problem(os.path.normpath(f"{path}/{OVERLAY}/{inner}"), reason)
            elif kind == "file" and entry.endswith(".yaml"):
                blueprint = self.model(path, Blueprint)
                if blueprint is not None and f"{blueprint.name}.yaml" != entry:
                    self.problem(
                        path, f"the file of the blueprint {blueprint.name} has another name"
                    )
                elif blueprint is not None:
                    blueprints[blueprint.name] = blueprint
            elif kind == "link":
                self.problem(path, "a link, not a file or a directory")
            else:
                self.problem(
                    path, "neither a blueprint's .yaml file nor a derived image's directory"
                )
        return blueprints, overlays

    def scenarios(self) -> list[ScenarioVersion]:
        found: list[tuple[str, int, ScenarioVersion]] = []
        for scenario_id in self.directory(SCENARIOS) or []:
            versions = self.directory(f"{SCENARIOS}/{scenario_id}")
            if versions == []:
                self.problem(
                    f"{SCENARIOS}/{scenario_id}", "a scenario's directory without a version"
                )
            for version in versions or []:
                path = f"{SCENARIOS}/{scenario_id}/{version}"
                entries = self.directory(path)
                if entries is None:
                    continue
                if not _VERSION.fullmatch(version):
                    self.problem(path, "a version's directory is named by a positive integer")
                    continue
                for extra in sorted(set(entries) - {SCENARIO, GROUND_TRUTH}):
                    self.problem(f"{path}/{extra}", f"not {SCENARIO} or {GROUND_TRUTH}")
                scenario = self.model(f"{path}/{SCENARIO}", Scenario)
                truth = self.model(f"{path}/{GROUND_TRUTH}", GroundTruth)
                if scenario is None or truth is None:
                    continue
                if (scenario.id, str(scenario.version)) != (scenario_id, version):
                    self.problem(
                        f"{path}/{SCENARIO}",
                        "the id and the version are not the names of the file's directories",
                    )
                    continue
                found.append(
                    (scenario.id, scenario.version, ScenarioVersion(path, scenario, truth))
                )
        return [version for _, _, version in sorted(found, key=lambda item: item[:2])]


def load_catalog(root: Path) -> Catalog:
    """Load the catalog checkout at ROOT.

    Args:
        root: The catalog's root (a link to it is followed).

    Returns:
        The catalog: every file read and valid on its own. What the files
        say about each other is the lint's matter.

    Raises:
        CatalogError: ROOT is missing, cannot be read or is not a
            directory, the layout is not the catalog's, or a file is not
            YAML of the subset or does not match its model. It names every
            problem, each with its file or directory.
    """
    try:
        root = root.resolve(strict=True)
    except FileNotFoundError:
        raise CatalogError([(".", "missing")]) from None
    except (OSError, RuntimeError, ValueError) as exc:
        raise CatalogError([(".", f"cannot be read ({type(exc).__name__})")]) from None
    if not root.is_dir():
        raise CatalogError([(".", "not a directory")])
    reader = _Reader(root)
    taxonomy = reader.model(TAXONOMY, Taxonomy)
    blueprints, overlays = reader.blueprints()
    scenarios = reader.scenarios()
    if reader.problems or taxonomy is None:
        raise CatalogError(reader.problems)
    return Catalog(root, taxonomy, blueprints, overlays, tuple(scenarios))
