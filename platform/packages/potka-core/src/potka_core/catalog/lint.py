"""The catalog lint: what a catalog checkout must satisfy beyond each file's form.

`lint` loads the checkout and reports findings, each with the file or
directory it is about and the rule it breaks. When a file cannot be loaded
the findings are the loader's (rule ``format``) and no other rule runs: the
rules read a catalog whose every file has its form.

Some rules compare the catalog with the golden images table
(`potka_core.images`): the infrastructure's blueprints, their versions,
types and packages.

The findings name scenario IDs and labels of ground truths: they are for
the catalog's authors and never part of what a model reads.

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
``golden``
    Every golden image a blueprint description names, for a node or for
    the probe client, is a blueprint of the golden images table at the
    version the table lists.
``aliases``
    Every segment of a golden image's alias (``<blueprint>/<version>``)
    begins and ends with a letter or a digit and holds only printable
    ASCII characters other than the space and the slash
    (`potka_core.images.alias_segment`). A derived image's alias
    (``potka/<name>/<version>``) is of that kind by the form of its name
    and version.
``isolation``
    A scenario's ``isolation`` is the type of the golden image of every
    node of its blueprint.
``available``
    Every fault primitive a scenario's ``setup`` and ``inject`` steps use
    is available: ``net.block_port`` only when every golden image carries
    ``nftables``, and a load or exhaustion primitive (`LOAD_PRIMITIVES`)
    only when derived images can be built (`OFFLINE_BUILD_PROFILE`).
``load``
    A load or exhaustion primitive works through a background component's
    setting. Its step names, each outright and not through a parameter, a
    ``node`` of the scenario's blueprint, a ``component`` of that node and
    a ``setting`` the component declares, and gives a ``value`` that in no
    instance is the setting's normal value: neither the same text, written
    out, nor the same number, whether each is a number or a text that
    writes one (``"01"``, ``" 1"`` and ``1.0`` are the number 1). A value
    with too many combinations of parameter values to compare is a
    finding.
``paths``
    Within each split, every load or exhaustion primitive that scenarios
    other than controls use reaches at least two components on at least
    two blueprints; one component name on two blueprints is one component.
    A scenario counts as its newest version has it.
``ticket``
    A scenario's ticket holds none of: the ID of a scenario of the catalog
    or a fault primitive's name (as a whole word, in any case); a label of
    its own ground truth other than ``none`` and ``unknown`` (likewise);
    ``pokus``, ``pinka`` or ``potka`` (anywhere, in any case). It is read
    as it is written, a parameter's name aside, and as its instances
    read: with every combination of the values of the choices it uses, a
    range at its smallest value. A ticket with too many combinations to
    read is a finding, and is still read as it is written.
``leakage``
    What an overlay adds to an image tells nothing of the scenarios: no
    file's path below the overlay and no file's content holds the ID of a
    scenario, a fault primitive's name or a label a ground truth uses
    other than ``none`` and ``unknown`` (as a whole word, in any case), nor
    ``accepted_labels``, ``then_required``, ``resolution_state``,
    ``pokus``, ``pinka`` or ``potka`` (anywhere, in any case); and no path
    has a ``.git`` component, in any case.

A whole word is not preceded or followed by a letter, a digit or an
underscore.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import product
from pathlib import Path

from pydantic import JsonValue

from potka_core.catalog.blueprint import Blueprint, Golden, Node
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
from potka_core.catalog.parameters import Choice, Range, Scalar, placeholders, render_text
from potka_core.catalog.scenario import Scenario
from potka_core.catalog.steps import Probe, Step
from potka_core.catalog.taxonomy import NONE, UNKNOWN
from potka_core.images import GoldenImages, alias_segment, load_table

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
# The load and exhaustion primitives: each changes a setting of a background component.
LOAD_PRIMITIVES = frozenset(
    {
        "pg.exhaust_connections",
        "proc.cpu_hog",
        "proc.fd_exhaust",
        "proc.mem_hog",
        "redis.maxmemory_oom",
    }
)
# The profile without a network device in which derived images are built, by the name
# the infrastructure's contract gives it; None while the contract names none. No derived
# image is built then, no background component runs, and the load and exhaustion
# primitives are unavailable.
OFFLINE_BUILD_PROFILE: str | None = None
# The package a primitive's tool comes from: the primitive is available only when every
# golden image carries it, so that the tool's presence tells nothing about the fault.
TOOLS = {"net.block_port": "nftables"}
# What names the lab or the platform.
NAMES = ("pokus", "pinka", "potka")
# The field names of a ground truth that are no common word.
FIELDS = ("accepted_labels", "then_required", "resolution_state")
# How many combinations of parameter values are read for one text: a step's ``value``,
# compared with a normal value, or a ticket. More are a finding, never a text left unread.
MAX_COMBINATIONS = 10_000
# A text that writes a number, as a component may read a setting: white space around it, a
# sign, digits with or without a point, an exponent.
_NUMBER = re.compile(r"\s*[-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?\s*")
_RANGE_DIGITS = 16


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


def _goldens(blueprint: Blueprint) -> Iterator[tuple[str, Golden]]:
    """Every golden image the description names, each with whose it is."""
    yield "the probe client", blueprint.probe_client
    for node in blueprint.nodes:
        yield f"the node {node.alias}", node.golden


def _golden(catalog: Catalog, table: GoldenImages) -> Iterator[Finding]:
    for name, blueprint in catalog.blueprints.items():
        for who, golden in _goldens(blueprint):
            if table.blueprint(golden.blueprint, golden.version) is not None:
                continue
            listed = table.blueprints.get(golden.blueprint)
            reason = (
                "it has no blueprint of that name"
                if listed is None
                else f"it lists {golden.blueprint} at the version {listed.version}"
            )
            yield Finding(
                f"{BLUEPRINTS}/{name}.yaml",
                "golden",
                f"the golden image of {who} is not in the golden images table: {reason}",
            )


def _aliases(catalog: Catalog) -> Iterator[Finding]:
    for name, blueprint in catalog.blueprints.items():
        for who, golden in _goldens(blueprint):
            for part, text in (("blueprint", golden.blueprint), ("version", golden.version)):
                if not alias_segment(text):
                    yield Finding(
                        f"{BLUEPRINTS}/{name}.yaml",
                        "aliases",
                        f"the golden image of {who}: its {part} is no segment of an alias: it"
                        " does not begin and end with a letter or a digit, or holds a character"
                        " other than printable ASCII without the space and the slash",
                    )


def _isolation(catalog: Catalog, table: GoldenImages) -> Iterator[Finding]:
    for version in catalog.scenarios:
        scenario = version.scenario
        blueprint = catalog.blueprints.get(scenario.blueprint)
        for node in blueprint.nodes if blueprint is not None else ():
            golden = table.blueprint(node.golden.blueprint, node.golden.version)
            # A golden image the table does not list is the rule golden's finding.
            if golden is not None and golden.type != scenario.isolation:
                yield Finding(
                    _scenario(version),
                    "isolation",
                    f"its isolation is {scenario.isolation}, but the node {node.alias} of the"
                    f" blueprint {scenario.blueprint} has a golden image of the type {golden.type}",
                )


def _faults(scenario: Scenario) -> Iterator[tuple[str, Step]]:
    """The scenario's fault primitive steps, each with its place."""
    for place, step in _placed(setup=scenario.setup, inject=scenario.inject):
        if isinstance(step, Step):
            yield place, step


def _available(catalog: Catalog, table: GoldenImages) -> Iterator[Finding]:
    for version in catalog.scenarios:
        for place, step in _faults(version.scenario):
            package = TOOLS.get(step.primitive)
            if package is not None and not table.everywhere(package):
                yield Finding(
                    _scenario(version),
                    "available",
                    f"{place}: {step.primitive} is not available: not every golden image"
                    f" carries {package}",
                )
            if step.primitive in LOAD_PRIMITIVES and OFFLINE_BUILD_PROFILE is None:
                yield Finding(
                    _scenario(version),
                    "available",
                    f"{place}: {step.primitive} is not available: no derived image can be"
                    " built yet, so no background component runs",
                )


def _outright(step: Step, key: str) -> str | None:
    """The step's parameter KEY when it is a string that uses no scenario parameter."""
    value = step.params.get(key)
    return value if isinstance(value, str) and not placeholders(value) else None


def _text(value: Scalar) -> str:
    """VALUE written out, as a template writes it: a string as it is, any other as JSON."""
    return value if isinstance(value, str) else json.dumps(value)


def _number(value: Scalar) -> Decimal | None:
    """VALUE as a number: a number itself, or a text that writes one; None for any other."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    # A float by its shortest text, so that 0.1 is the number the text 0.1 writes.
    text = repr(value) if isinstance(value, float) else value
    if _NUMBER.fullmatch(text) is None:
        return None
    try:
        return Decimal(text.strip())
    except ArithmeticError:
        return None


def _same(one: Scalar, other: Scalar) -> bool:
    """Whether ONE and OTHER are one value: the same text written out, or the same number."""
    if _text(one) == _text(other):
        return True
    first, second = _number(one), _number(other)
    return first is not None and second is not None and first == second


def _in_range(normal: Scalar, parameter: Range) -> bool:
    """Whether a value of the range is the same as NORMAL (`_same`)."""
    number = _number(normal)
    # A range's values are whole numbers of at most sixteen digits.
    if number is None or not number.is_finite() or number.adjusted() > _RANGE_DIGITS:
        return False
    if number != number.to_integral_value():
        return False
    return int(number) in parameter.candidates()


def _reaches(
    value: Scalar, normal: Scalar, parameters: Mapping[str, Choice | Range]
) -> bool | None:
    """Whether VALUE is NORMAL in some instance; None when there are too many to say."""
    if not isinstance(value, str):
        return _same(value, normal)
    used = tuple(dict.fromkeys(placeholders(value)))
    if not used:
        return _same(render_text(value, {}), normal)
    if value == f"${{{used[0]}}}":
        # One placeholder and nothing else: the parameter's value itself.
        parameter = parameters[used[0]]
        if isinstance(parameter, Range):
            return _in_range(normal, parameter)
        return any(_same(candidate, normal) for candidate in parameter.values)
    candidates = [parameters[name].candidates() for name in used]
    if math.prod(len(values) for values in candidates) > MAX_COMBINATIONS:
        return None
    return any(
        _same(render_text(value, dict(zip(used, combination, strict=True))), normal)
        for combination in product(*candidates)
    )


def _load_problem(step: Step, scenario: Scenario, blueprint: Blueprint) -> str | None:
    """What is wrong with a load or exhaustion primitive's step, if anything."""
    given = {key: _outright(step, key) for key in ("node", "component", "setting")}
    unnamed = [key for key, name in given.items() if name is None]
    if unnamed:
        return f"it must name its {', '.join(unnamed)} outright, as text without a parameter"
    node = blueprint.node(given["node"] or "")
    if node is None:
        return f"its node is no node of the blueprint {blueprint.name}"
    component = node.component(given["component"] or "")
    if component is None:
        return f"its component is no background component of the node {node.alias}"
    setting = given["setting"] or ""
    if setting not in component.settings:
        return f"its setting is no setting of the component {component.name}"
    value: JsonValue = step.params.get("value")
    if value is None or isinstance(value, dict | list):
        return "it must give a value: a string, a number or a boolean"
    reaches = _reaches(value, component.settings[setting], scenario.parameters)
    if reaches is None:
        return (
            f"its value has more than {MAX_COMBINATIONS} combinations of parameter values,"
            f" too many to compare with the normal value of {setting}"
        )
    if reaches:
        return (
            f"its value is the normal value of {setting}"
            if not (isinstance(value, str) and placeholders(value))
            else f"its value is, in an instance, the normal value of {setting}"
        )
    return None


def _load(catalog: Catalog) -> Iterator[Finding]:
    for version in catalog.scenarios:
        scenario = version.scenario
        blueprint = catalog.blueprints.get(scenario.blueprint)
        if blueprint is None:
            # The rule blueprint's finding.
            continue
        for place, step in _faults(scenario):
            if step.primitive not in LOAD_PRIMITIVES:
                continue
            problem = _load_problem(step, scenario, blueprint)
            if problem is not None:
                yield Finding(_scenario(version), "load", f"{place}: {step.primitive}: {problem}")


def _paths(catalog: Catalog) -> Iterator[Finding]:
    newest: dict[str, Scenario] = {}
    for version in catalog.scenarios:
        known = newest.get(version.scenario.id)
        if known is None or version.scenario.version > known.version:
            newest[version.scenario.id] = version.scenario
    reached: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for scenario in newest.values():
        if is_control(scenario.id):
            continue
        for _, step in _faults(scenario):
            component = _outright(step, "component")
            if step.primitive in LOAD_PRIMITIVES and component is not None:
                reached.setdefault((scenario.split, step.primitive), set()).add(
                    (component, scenario.blueprint)
                )
    for (split, primitive), pairs in sorted(reached.items()):
        components = len({component for component, _ in pairs})
        blueprints = len({blueprint for _, blueprint in pairs})
        if components < 2 or blueprints < 2:
            yield Finding(
                SCENARIOS,
                "paths",
                f"in the split {split}, {primitive} reaches {components} component(s) on"
                f" {blueprints} blueprint(s): it must reach at least two components on at"
                " least two blueprints",
            )


@dataclass(frozen=True)
class _Term:
    """Something a ticket or an overlay must not hold.

    Attributes:
        what: How a finding names it.
        pattern: What finds it.
    """

    what: str
    pattern: re.Pattern[str]


def _anywhere(what: str, text: str) -> _Term:
    return _Term(what, re.compile(re.escape(text), re.IGNORECASE))


def _word(what: str, text: str) -> _Term:
    return _Term(what, re.compile(rf"(?<!\w){re.escape(text)}(?!\w)", re.IGNORECASE))


def _common_terms(catalog: Catalog) -> list[_Term]:
    """The terms of tickets and overlays alike: the names, scenario IDs and primitive names."""
    ids = sorted({version.scenario.id for version in catalog.scenarios})
    return [
        *(_anywhere(f"the string {name}", name) for name in NAMES),
        *(_word(f"the scenario ID {scenario_id}", scenario_id) for scenario_id in ids),
        *(_word(f"the fault primitive name {name}", name) for name in sorted(PRIMITIVES)),
    ]


def _labels_of(truths: Iterable[ScenarioVersion]) -> list[str]:
    """The labels the ground truths use, other than none and unknown."""
    used = {label for version in truths for label in version.ground_truth.labels()}
    return sorted(used - {NONE, UNKNOWN})


def _as_written(scenario: Scenario) -> str:
    """The ticket with ``${}`` for every placeholder: a parameter's name is no text of it."""
    used = dict.fromkeys(placeholders(scenario.ticket), "${}")
    return scenario.render_ticket(used)


def _instances(scenario: Scenario) -> list[str] | None:
    """The ticket as its instances read; None when they are too many.

    An instance's text has a value of each choice the ticket uses, in every
    combination, and the smallest value of each range.
    """
    used = tuple(dict.fromkeys(placeholders(scenario.ticket)))
    values: list[Sequence[Scalar]] = []
    for name in used:
        parameter = scenario.parameters[name]
        values.append(
            parameter.values if isinstance(parameter, Choice) else parameter.candidates()[:1]
        )
    if math.prod(len(candidates) for candidates in values) > MAX_COMBINATIONS:
        return None
    return [
        scenario.render_ticket(dict(zip(used, combination, strict=True)))
        for combination in product(*values)
    ]


def _ticket(catalog: Catalog) -> Iterator[Finding]:
    common = _common_terms(catalog)
    for version in catalog.scenarios:
        texts = [_as_written(version.scenario)]
        instances = _instances(version.scenario)
        if instances is None:
            yield Finding(
                _scenario(version),
                "ticket",
                f"the ticket has more than {MAX_COMBINATIONS} combinations of the values of"
                " its choices, too many to read",
            )
        else:
            texts += instances
        terms = [
            *common,
            *(
                _word(f"the label {label} of its ground truth", label)
                for label in _labels_of([version])
            ),
        ]
        for term in terms:
            if any(term.pattern.search(text) for text in texts):
                yield Finding(_scenario(version), "ticket", f"the ticket holds {term.what}")


def _leakage(catalog: Catalog) -> Iterator[Finding]:
    terms = [
        *_common_terms(catalog),
        *(_anywhere(f"the ground truths' field name {name}", name) for name in FIELDS),
        *(
            _word(f"the label {label}, which a ground truth uses", label)
            for label in _labels_of(catalog.scenarios)
        ),
    ]
    for name, overlay in sorted(catalog.overlays.items()):
        for path in overlay.paths():
            inside = path.relative_to(overlay.root).as_posix()
            where = f"{BLUEPRINTS}/{name}/{OVERLAY}/{inside}"
            if ".git" in inside.lower().split("/"):
                yield Finding(where, "leakage", "its path has a .git component")
            for term in terms:
                if term.pattern.search(inside):
                    yield Finding(where, "leakage", f"its path holds {term.what}")
            try:
                text = path.read_bytes().decode("utf-8", errors="replace")
            except OSError as exc:
                yield Finding(where, "leakage", f"cannot be read ({type(exc).__name__})")
                continue
            for term in terms:
                match = term.pattern.search(text)
                if match is not None:
                    line = text.count("\n", 0, match.start()) + 1
                    yield Finding(where, "leakage", f"line {line} holds {term.what}")


RULES: tuple[Callable[[Catalog], Iterator[Finding]], ...] = (
    _split,
    _blueprint,
    _names,
    _templates,
    _control,
    _probes,
    _labels,
    _overlay,
    _aliases,
    _load,
    _paths,
    _ticket,
    _leakage,
)
# The rules that compare the catalog with the golden images table.
TABLE_RULES: tuple[Callable[[Catalog, GoldenImages], Iterator[Finding]], ...] = (
    _golden,
    _isolation,
    _available,
)


def lint_catalog(catalog: Catalog, table: GoldenImages) -> list[Finding]:
    """Every finding of the rules in a loaded catalog.

    Args:
        catalog: The catalog.
        table: The golden images table.

    Returns:
        The findings, sorted by path, rule and message; empty when the
        catalog satisfies every rule.
    """
    found = {finding for rule in RULES for finding in rule(catalog)}
    found |= {finding for rule in TABLE_RULES for finding in rule(catalog, table)}
    return sorted(found)


def lint(root: Path, table: GoldenImages | None = None) -> list[Finding]:
    """Load the catalog checkout at ROOT and check it.

    Args:
        root: The catalog's root.
        table: The golden images table; None for the release's own
            (`potka_core.images.load_table`).

    Returns:
        The findings, sorted by path, rule and message: the loader's when a
        file cannot be loaded (rule ``format``), else the rules'.

    Raises:
        OSError: TABLE is None and the release's table cannot be read.
        pydantic.ValidationError: TABLE is None and the release's file is
            not a table.
    """
    if table is None:
        table = load_table()
    try:
        catalog = load_catalog(root)
    except CatalogError as exc:
        return sorted({Finding(path, "format", reason) for path, reason in exc.problems})
    return lint_catalog(catalog, table)
