"""The catalog lint: what a catalog checkout must satisfy beyond each file's form.

`lint` loads the checkout and reports findings, each with the file or
directory it is about and the rule it breaks. When a file cannot be loaded
the findings are the loader's (rule ``format``) and no other rule runs: the
rules read a catalog whose every file has its form.

The rules:

``format``
    The layout is the catalog's, and every file is YAML of the subset and
    matches its model (`potka_core.catalog.loader`).
``split``
    Every version of a scenario is in the same split: the scenario's ID is
    its template's identity, and a template belongs to one split.
``blueprint``
    A scenario's blueprint has a description in ``blueprints/``.
``names``
    Every fault primitive and every probe a step names exists.
``templates``
    A ground truth's templates use only parameters its scenario declares,
    so every instance resolves (a scenario's own templates are checked by
    its model).
``control``
    A control scenario (its ID, without a leading ``FIXTURE-``, begins with
    ``CONTROL-``) accepts the label ``none`` and no other, and has no
    acceptable action and no ``inject`` step (what its ``setup`` steps do
    is not judged); no other scenario accepts the label ``none``.
``probes``
    Every scenario has a confirm probe, and every ground truth a
    verification probe.
``labels``
    Every label a ground truth uses is one the taxonomy lists.
``overlay``
    Every derived image a node names has its overlay, every overlay
    belongs to a derived image some node names, and the nodes that share a
    derived image describe it alike: its version, its golden image and its
    background components with their settings files and settings (its
    services, read and patch paths and endpoints are its blueprint's own).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from potka_core.catalog.blueprint import Node
from potka_core.catalog.loader import (
    BLUEPRINTS,
    GROUND_TRUTH,
    OVERLAY,
    SCENARIO,
    SCENARIOS,
    Catalog,
    CatalogError,
    ScenarioVersion,
    load_catalog,
)
from potka_core.catalog.steps import Probe, Step
from potka_core.catalog.taxonomy import NONE

# The fault primitives of the injector and the probes of the verifier, by name.
PRIMITIVES = frozenset(
    {
        "app.bad_config",
        "disk.fill",
        "disk.inode_exhaust",
        "dns.break_resolver",
        "dns.wrong_record",
        "docker.crashloop",
        "docker.kill_container",
        "docker.stop_daemon",
        "file.mutate",
        "file.write",
        "net.block_port",
        "net.netem",
        "nginx.corrupt_conf",
        "nginx.set_upstream",
        "permissions.break",
        "pg.break_hba",
        "pg.exhaust_connections",
        "pg.fill_disk",
        "pg.stop",
        "proc.cpu_hog",
        "proc.fd_exhaust",
        "proc.mem_hog",
        "redis.maxmemory_oom",
        "redis.stop",
        "systemd.action",
        "systemd.break_unit",
    }
)
PROBES = frozenset(
    {
        "container_state",
        "disk_usage_below",
        "dns_resolve",
        "file_unchanged",
        "http_get",
        "inode_usage_below",
        "integrity",
        "load_below",
        "nginx_config_valid",
        "pg_query",
        "pg_ready",
        "process_absent",
        "redis_ping",
        "tcp_connect",
        "unit_state",
    }
)
FIXTURE_PREFIX = "FIXTURE-"
CONTROL_PREFIX = "CONTROL-"


@dataclass(frozen=True, order=True)
class Finding:
    """One thing the lint found.

    Attributes:
        path: The file or directory it is about, relative to the catalog's root.
        rule: The rule it breaks (the module's docstring lists them).
        message: What is wrong.
    """

    path: str
    rule: str
    message: str


def is_control(scenario_id: str) -> bool:
    """Whether SCENARIO_ID names a control scenario.

    Args:
        scenario_id: A scenario's ID.

    Returns:
        True when the ID, without a leading ``FIXTURE-``, begins with ``CONTROL-``.
    """
    return scenario_id.removeprefix(FIXTURE_PREFIX).startswith(CONTROL_PREFIX)


def _scenario(version: ScenarioVersion) -> str:
    return f"{version.path}/{SCENARIO}"


def _truth(version: ScenarioVersion) -> str:
    return f"{version.path}/{GROUND_TRUTH}"


def _split(catalog: Catalog) -> Iterator[Finding]:
    splits: dict[str, dict[int, str]] = {}
    for version in catalog.scenarios:
        scenario = version.scenario
        splits.setdefault(scenario.id, {})[scenario.version] = scenario.split
    for scenario_id, by_version in splits.items():
        if len(set(by_version.values())) > 1:
            listed = ", ".join(
                f"{number} in {split}" for number, split in sorted(by_version.items())
            )
            yield Finding(
                f"{SCENARIOS}/{scenario_id}",
                "split",
                f"its versions are in different splits: {listed}",
            )


def _blueprint(catalog: Catalog) -> Iterator[Finding]:
    for version in catalog.scenarios:
        name = version.scenario.blueprint
        if name not in catalog.blueprints:
            yield Finding(
                _scenario(version), "blueprint", f"the blueprint {name} is not in {BLUEPRINTS}/"
            )


def _unknown(path: str, steps: Iterable[tuple[str, Probe | Step]]) -> Iterator[Finding]:
    for place, step in steps:
        if isinstance(step, Step) and step.primitive not in PRIMITIVES:
            yield Finding(path, "names", f"{place}: no fault primitive {step.primitive}")
        if isinstance(step, Probe) and step.probe not in PROBES:
            yield Finding(path, "names", f"{place}: no probe {step.probe}")


def _placed(**phases: tuple[Probe | Step, ...]) -> Iterator[tuple[str, Probe | Step]]:
    for phase, steps in phases.items():
        for index, step in enumerate(steps):
            yield f"{phase}.{index}", step


def _names(catalog: Catalog) -> Iterator[Finding]:
    for name, blueprint in catalog.blueprints.items():
        yield from _unknown(f"{BLUEPRINTS}/{name}.yaml", _placed(health=blueprint.health))
    for version in catalog.scenarios:
        scenario, truth = version.scenario, version.ground_truth
        yield from _unknown(
            _scenario(version),
            _placed(
                preconditions=scenario.preconditions,
                setup=scenario.setup,
                inject=scenario.inject,
                confirm=scenario.confirm,
            ),
        )
        yield from _unknown(
            _truth(version),
            _placed(verification=truth.verification, collateral=truth.collateral),
        )


def _templates(catalog: Catalog) -> Iterator[Finding]:
    for version in catalog.scenarios:
        declared = set(version.scenario.parameters)
        for name in sorted(set(version.ground_truth.placeholders()) - declared):
            yield Finding(
                _truth(version),
                "templates",
                f"a template uses the parameter {name}, which the scenario does not declare",
            )


def _control(catalog: Catalog) -> Iterator[Finding]:
    for version in catalog.scenarios:
        truth = version.ground_truth
        if not is_control(version.scenario.id):
            if NONE in truth.diagnosis.accepted_labels:
                yield Finding(
                    _truth(version),
                    "control",
                    f"only a control scenario (one whose ID begins with {CONTROL_PREFIX} or"
                    f" {FIXTURE_PREFIX}{CONTROL_PREFIX}) accepts the label {NONE}",
                )
            continue
        if truth.diagnosis.accepted_labels != (NONE,):
            yield Finding(
                _truth(version),
                "control",
                f"a control scenario must accept the label {NONE} and no other",
            )
        if truth.actions.acceptable:
            yield Finding(
                _truth(version), "control", "a control scenario must have no acceptable action"
            )
        if version.scenario.inject:
            yield Finding(
                _scenario(version), "control", "a control scenario must have no inject step"
            )


def _probes(catalog: Catalog) -> Iterator[Finding]:
    for version in catalog.scenarios:
        if not version.scenario.confirm:
            yield Finding(_scenario(version), "probes", "no confirm probe")
        if not version.ground_truth.verification:
            yield Finding(_truth(version), "probes", "no verification probe")


def _labels(catalog: Catalog) -> Iterator[Finding]:
    known = set(catalog.taxonomy.names())
    for version in catalog.scenarios:
        for label in sorted(set(version.ground_truth.labels()) - known):
            yield Finding(_truth(version), "labels", f"the label {label} is not in the taxonomy")


def _described(node: Node) -> dict[str, str]:
    """Each background component of NODE as JSON with sorted keys, by its name.

    The order of the settings in a file does not matter; 1, 1.0 and true
    are different normal values.
    """
    return {
        component.name: json.dumps(component.model_dump(mode="json"), sort_keys=True)
        for component in node.components
    }


def _differences(first: Node, other: Node) -> list[str]:
    """How OTHER describes the derived image it shares with FIRST differently."""
    differences = []
    if first.derived is not None and other.derived is not None:
        if other.derived.version != first.derived.version:
            differences.append(f"version {other.derived.version}, not {first.derived.version}")
    if other.golden != first.golden:
        differences.append(
            f"built on {other.golden.blueprint}/{other.golden.version},"
            f" not {first.golden.blueprint}/{first.golden.version}"
        )
    ours, theirs = _described(other), _described(first)
    for name in sorted(set(ours) | set(theirs)):
        if name not in ours:
            differences.append(
                f"it does not describe the background component {name}, which {first.alias} does"
            )
        elif name not in theirs:
            differences.append(
                f"it describes the background component {name}, which {first.alias} does not"
            )
        elif ours[name] != theirs[name]:
            differences.append(f"another settings file or other settings of {name}")
    return differences


def _overlay(catalog: Catalog) -> Iterator[Finding]:
    users: dict[str, list[tuple[str, Node]]] = {}
    for name, blueprint in sorted(catalog.blueprints.items()):
        for node in blueprint.nodes:
            if node.derived is None:
                continue
            where = f"{BLUEPRINTS}/{name}.yaml"
            users.setdefault(node.derived.name, []).append((where, node))
            if node.derived.name not in catalog.overlays:
                yield Finding(
                    where,
                    "overlay",
                    f"the derived image {node.derived.name} of the node {node.alias} has no"
                    f" overlay ({BLUEPRINTS}/{node.derived.name}/{OVERLAY})",
                )
    for derived, nodes in sorted(users.items()):
        first_where, first = nodes[0]
        for where, node in nodes[1:]:
            differences = _differences(first, node)
            if differences:
                yield Finding(
                    where,
                    "overlay",
                    f"the node {node.alias} describes the derived image {derived} differently"
                    f" from the node {first.alias} of {first_where}: {'; '.join(differences)}",
                )
    for derived in sorted(set(catalog.overlays) - set(users)):
        yield Finding(
            f"{BLUEPRINTS}/{derived}/{OVERLAY}", "overlay", "no node names this derived image"
        )


RULES: tuple[Callable[[Catalog], Iterator[Finding]], ...] = (
    _split,
    _blueprint,
    _names,
    _templates,
    _control,
    _probes,
    _labels,
    _overlay,
)


def lint_catalog(catalog: Catalog) -> list[Finding]:
    """Every finding of the rules in a loaded catalog.

    Args:
        catalog: The catalog.

    Returns:
        The findings, sorted by path, rule and message; empty when the
        catalog satisfies every rule.
    """
    return sorted({finding for rule in RULES for finding in rule(catalog)})


def lint(root: Path) -> list[Finding]:
    """Load the catalog checkout at ROOT and check it.

    Args:
        root: The catalog's root.

    Returns:
        The findings, sorted by path, rule and message: the loader's when a
        file cannot be loaded (rule ``format``), else the rules'.
    """
    try:
        catalog = load_catalog(root)
    except CatalogError as exc:
        return sorted({Finding(path, "format", reason) for path, reason in exc.problems})
    return lint_catalog(catalog)
