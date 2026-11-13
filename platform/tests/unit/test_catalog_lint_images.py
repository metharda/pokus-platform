"""The catalog lint's rules on images, fault primitives' availability, tickets and overlays."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from hypothesis import assume, given
from hypothesis import strategies as st
from potka_core.catalog import lint as lint_module
from potka_core.catalog.blueprint import Derived
from potka_core.catalog.lint import LOAD_PRIMITIVES, PRIMITIVES, lint, lint_catalog
from potka_core.catalog.loader import load_catalog
from potka_core.images import GoldenImages, alias_segment, load_table
from potka_testing.fixture_catalog import FIXTURE_CATALOG, copy_fixture, edit_yaml
from pydantic import ValidationError

WEB = "blueprints/fixture-web.yaml"
STORE = "blueprints/fixture-store.yaml"
ONE = "scenarios/FIXTURE-001/1/scenario.yaml"
TWO = "scenarios/FIXTURE-002/1/scenario.yaml"
CONTROL = "scenarios/FIXTURE-CONTROL-000/1/scenario.yaml"
WEB_OVERLAY = "blueprints/fixture-web-app/overlay"
STORE_OVERLAY = "blueprints/fixture-store-app/overlay"
# The golden version the fixture's blueprints name, and one no table here lists.
GOLDEN: str = yaml.safe_load((FIXTURE_CATALOG / WEB).read_text("utf-8"))["probe_client"]["version"]
OTHER = "0.0.9"
NOT_LISTED = "is not in the golden images table"
NOT_A_SEGMENT = (
    "is no segment of an alias: it does not begin and end with a letter or a digit, or holds a"
    " character other than printable ASCII without the space and the slash"
)


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    return copy_fixture(tmp_path / "catalog")


@pytest.fixture
def profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Derived images can be built: the load and exhaustion primitives are available."""
    monkeypatch.setattr(lint_module, "OFFLINE_BUILD_PROFILE", "some-profile")


def table(change: Callable[[dict[str, Any]], object] | None = None) -> GoldenImages:
    """The release's golden images table, with CHANGE applied to its blueprints."""
    data = load_table().model_dump(mode="json")
    if change is not None:
        change(data["blueprints"])
    return GoldenImages.model_validate_json(json.dumps(data), strict=True)


def found(root: Path, images: GoldenImages | None = None) -> list[tuple[str, str, str]]:
    findings = lint(root, images)
    assert findings == sorted(set(findings))
    return [(finding.path, finding.rule, finding.message) for finding in findings]


def messages(root: Path, rule: str, images: GoldenImages | None = None) -> list[str]:
    """The messages of the rule RULE's findings; every other finding is of a listed rule."""
    return [message for _, found_rule, message in found(root, images) if found_rule == rule]


def test_the_fixture_catalog_passes_with_the_release_s_table_and_with_one_given(
    catalog: Path,
) -> None:
    assert lint(catalog) == []
    assert lint(catalog, load_table()) == []
    assert lint_catalog(load_catalog(catalog), table()) == []
    assert load_table().blueprint("web-nginx-flask", GOLDEN) is not None
    assert load_table().blueprint("web-nginx-flask", OTHER) is None


# golden


def not_golden(path: str, who: str, reason: str) -> tuple[str, str, str]:
    return (path, "golden", f"the golden image of {who} {NOT_LISTED}: {reason}")


def test_every_golden_image_of_a_description_is_in_the_table(catalog: Path) -> None:
    edit_yaml(catalog, WEB, lambda d: d["nodes"][0]["golden"].update(version=OTHER))
    assert found(catalog) == [
        not_golden(WEB, "the node web", f"it lists web-nginx-flask at the version {GOLDEN}")
    ]
    edit_yaml(catalog, WEB, lambda d: d["nodes"][0]["golden"].update(blueprint="web-other"))
    assert found(catalog) == [not_golden(WEB, "the node web", "it has no blueprint of that name")]
    edit_yaml(
        catalog,
        WEB,
        lambda d: d["nodes"][0]["golden"].update(blueprint="Web-Nginx-Flask", version=GOLDEN),
    )
    assert found(catalog) == [not_golden(WEB, "the node web", "it has no blueprint of that name")]


def test_the_probe_client_s_golden_image_is_in_the_table_like_a_node_s(catalog: Path) -> None:
    edit_yaml(catalog, STORE, lambda d: d["probe_client"].update(version=OTHER))
    assert found(catalog) == [
        not_golden(STORE, "the probe client", f"it lists probe-client at the version {GOLDEN}")
    ]
    edit_yaml(catalog, STORE, lambda d: d["probe_client"].update(blueprint="prober"))
    assert found(catalog) == [
        not_golden(STORE, "the probe client", "it has no blueprint of that name")
    ]


def test_the_table_given_decides_what_is_golden(catalog: Path) -> None:
    def another_version(blueprints: dict[str, Any]) -> None:
        blueprints["web-nginx-flask"]["version"] = OTHER
        del blueprints["probe-client"]

    assert found(catalog, table(another_version)) == [
        not_golden(STORE, "the probe client", "it has no blueprint of that name"),
        not_golden(WEB, "the node web", f"it lists web-nginx-flask at the version {OTHER}"),
        not_golden(WEB, "the probe client", "it has no blueprint of that name"),
    ]
    edit_yaml(catalog, WEB, lambda d: d["nodes"][0]["golden"].update(version=OTHER))
    assert found(catalog, table(another_version)) == [
        not_golden(STORE, "the probe client", "it has no blueprint of that name"),
        not_golden(WEB, "the probe client", "it has no blueprint of that name"),
    ]


# aliases


@pytest.mark.parametrize(
    ("part", "text"),
    [
        ("blueprint", "web-"),
        ("blueprint", "-web"),
        ("blueprint", "web/nginx"),
        ("blueprint", "."),
        ("version", "1.0."),
        ("version", ".1"),
        ("version", "1/0"),
        ("version", "1.0.1 "),
        ("version", "1.0 .1"),
        ("blueprint", "web nginx"),
        ("blueprint", "w\u00e9b"),
    ],
)
def test_every_segment_of_a_golden_alias_begins_and_ends_with_a_letter_or_a_digit(
    catalog: Path, part: str, text: str
) -> None:
    def listed(blueprints: dict[str, Any]) -> None:
        # A table that lists the odd name or version: the form alone is the finding.
        if part == "blueprint":
            blueprints[text] = blueprints["web-nginx-flask"]
        else:
            blueprints["web-nginx-flask"]["version"] = text

    edit_yaml(catalog, WEB, lambda d: d["nodes"][0]["golden"].update({part: text}))
    assert found(catalog, table(listed)) == [
        (WEB, "aliases", f"the golden image of the node web: its {part} {NOT_A_SEGMENT}"),
    ]
    assert [rule for _, rule, _ in found(catalog)] == ["aliases", "golden"]


def test_the_probe_client_s_alias_is_checked_like_a_node_s(catalog: Path) -> None:
    edit_yaml(catalog, STORE, lambda d: d["probe_client"].update(version="1.0.1-"))
    assert found(catalog) == [
        (STORE, "aliases", f"the golden image of the probe client: its version {NOT_A_SEGMENT}"),
        not_golden(STORE, "the probe client", f"it lists probe-client at the version {GOLDEN}"),
    ]


def test_both_segments_are_checked(catalog: Path) -> None:
    edit_yaml(
        catalog, STORE, lambda d: d["nodes"][0]["golden"].update(blueprint="pg-", version="-1")
    )
    assert found(catalog) == [
        (STORE, "aliases", f"the golden image of the node db: its blueprint {NOT_A_SEGMENT}"),
        (STORE, "aliases", f"the golden image of the node db: its version {NOT_A_SEGMENT}"),
        not_golden(STORE, "the node db", "it has no blueprint of that name"),
    ]


# The form of a derived image's name, from the blueprint description's model itself.
DERIVED_NAME: str = Derived.model_json_schema()["properties"]["name"]["pattern"]


@given(
    name=st.from_regex(DERIVED_NAME.removeprefix("^").removesuffix("$"), fullmatch=True),
    version=st.integers(min_value=1, max_value=2**53 - 1),
)
def test_a_derived_image_s_name_and_version_are_alias_segments_by_their_form(
    name: str, version: int
) -> None:
    # Whatever the model accepts as a derived image's name and version is an alias
    # segment: potka/<name>/<version> needs no rule of its own.
    assume(len(name) <= 63)
    derived = Derived.model_validate_json(
        json.dumps({"name": name, "version": version}), strict=True
    )
    assert (derived.name, derived.version) == (name, version)
    assert alias_segment("potka")
    assert alias_segment(derived.name)
    assert alias_segment(str(derived.version))


def test_the_model_accepts_and_refuses_derived_names() -> None:
    assert DERIVED_NAME.startswith("^") and DERIVED_NAME.endswith("$")
    for name in ("a", "web-app", "a1-b2-c3"):
        assert Derived(name=name, version=1).name == name
    for name in ("", "-a", "a-", "a--b", "A", "a_b", "a b", "a/b", "a\n"):
        with pytest.raises(ValidationError):
            Derived.model_validate_json(json.dumps({"name": name, "version": 1}), strict=True)


# isolation


def a_machine(blueprints: dict[str, Any]) -> None:
    blueprints["pg-primary"]["type"] = "vm"


def isolated(path: str, isolation: str, node: str, blueprint: str, kind: str) -> Any:
    return (
        path,
        "isolation",
        f"its isolation is {isolation}, but the node {node} of the blueprint {blueprint} has a"
        f" golden image of the type {kind}",
    )


def test_a_scenario_s_isolation_is_the_type_of_its_nodes_golden_images(catalog: Path) -> None:
    edit_yaml(catalog, ONE, lambda d: d.update(isolation="vm"))
    assert found(catalog) == [isolated(ONE, "vm", "web", "fixture-web", "container")]
    assert found(catalog, table(a_machine)) == [
        isolated(ONE, "vm", "web", "fixture-web", "container"),
        isolated(TWO, "container", "db", "fixture-store", "vm"),
    ]
    edit_yaml(catalog, ONE, lambda d: d.update(isolation="container"))
    edit_yaml(catalog, TWO, lambda d: d.update(isolation="vm"))
    assert found(catalog, table(a_machine)) == []
    assert found(catalog) == [isolated(TWO, "vm", "db", "fixture-store", "container")]


def test_every_node_of_the_blueprint_has_the_scenario_s_isolation(catalog: Path) -> None:
    store = yaml.safe_load((catalog / STORE).read_text(encoding="utf-8"))["nodes"][0]
    edit_yaml(catalog, WEB, lambda d: d["nodes"].append(store))
    assert found(catalog) == []
    assert found(catalog, table(a_machine)) == [
        isolated(ONE, "container", "db", "fixture-web", "vm"),
        isolated(TWO, "container", "db", "fixture-store", "vm"),
        isolated(CONTROL, "container", "db", "fixture-web", "vm"),
    ]


def test_a_golden_image_the_table_does_not_list_has_no_type(catalog: Path) -> None:
    edit_yaml(catalog, ONE, lambda d: d.update(isolation="vm", blueprint="fixture-store"))
    edit_yaml(catalog, STORE, lambda d: d["nodes"][0]["golden"].update(version=OTHER))
    assert found(catalog) == [
        not_golden(STORE, "the node db", f"it lists pg-primary at the version {GOLDEN}")
    ]
    edit_yaml(catalog, ONE, lambda d: d.update(blueprint="gone"))
    assert [rule for _, rule, _ in found(catalog)] == ["golden", "blueprint"]


# available


def step(primitive: str, **params: Any) -> dict[str, Any]:
    return {"primitive": primitive, "params": params}


def add(root: Path, scenario: str, phase: str, *steps: dict[str, Any]) -> None:
    edit_yaml(root, scenario, lambda d: d[phase].extend(steps))


def with_nftables(*but: str) -> Callable[[dict[str, Any]], None]:
    """A change of the table: every blueprint carries nftables, but those of BUT."""

    def change(blueprints: dict[str, Any]) -> None:
        for name, blueprint in blueprints.items():
            carried = [
                p for p in blueprint["effective_packages"] if p.partition("=")[0] != "nftables"
            ]
            blueprint["effective_packages"] = carried if name in but else [*carried, "nftables"]

    return change


def test_a_port_is_blocked_only_when_every_golden_image_carries_nftables(catalog: Path) -> None:
    add(catalog, ONE, "inject", step("net.block_port", node="web", port=80))
    add(catalog, CONTROL, "setup", step("net.block_port", node="web", port=81))
    missing = "net.block_port is not available: not every golden image carries nftables"
    # Tables made here, so that the release's own table may come to carry nftables.
    everywhere = table(with_nftables())
    nowhere = table(with_nftables(*everywhere.blueprints))
    all_but_one = table(with_nftables("redis-cache"))
    assert everywhere.everywhere("nftables")
    assert not nowhere.everywhere("nftables")
    assert not all_but_one.everywhere("nftables")
    assert found(catalog, everywhere) == []
    for images in (nowhere, all_but_one):
        assert found(catalog, images) == [
            (ONE, "available", f"inject.2: {missing}"),
            (CONTROL, "available", f"setup.0: {missing}"),
        ]


def test_the_load_and_exhaustion_primitives_are_these_five() -> None:
    assert sorted(LOAD_PRIMITIVES) == [
        "pg.exhaust_connections",
        "proc.cpu_hog",
        "proc.fd_exhaust",
        "proc.mem_hog",
        "redis.maxmemory_oom",
    ]
    assert LOAD_PRIMITIVES < PRIMITIVES
    assert lint_module.OFFLINE_BUILD_PROFILE is None
    assert lint_module.TOOLS == {"net.block_port": "nftables"}


WORKERS = {"node": "web", "component": "report-builder", "setting": "workers", "value": 64}
PARALLEL = {"node": "db", "component": "ledger-compactor", "setting": "parallel", "value": 32}


def two_paths(root: Path, primitive: str = "proc.cpu_hog") -> None:
    """PRIMITIVE through two components on two blueprints, in scenarios that are no controls."""
    add(root, ONE, "inject", step(primitive, **WORKERS))
    add(root, TWO, "inject", step(primitive, **PARALLEL))


@pytest.mark.parametrize("primitive", sorted(LOAD_PRIMITIVES))
def test_a_load_primitive_is_refused_while_no_derived_image_can_be_built(
    catalog: Path, primitive: str
) -> None:
    two_paths(catalog, primitive)
    add(catalog, CONTROL, "setup", step(primitive, **WORKERS))
    unavailable = (
        f"{primitive} is not available: no derived image can be built yet, so no background"
        " component runs"
    )
    assert found(catalog) == [
        (ONE, "available", f"inject.2: {unavailable}"),
        (TWO, "available", f"inject.1: {unavailable}"),
        (CONTROL, "available", f"setup.0: {unavailable}"),
    ]


@pytest.mark.parametrize("primitive", sorted(LOAD_PRIMITIVES))
@pytest.mark.usefixtures("profile")
def test_a_load_primitive_passes_once_derived_images_can_be_built(
    catalog: Path, primitive: str
) -> None:
    two_paths(catalog, primitive)
    add(catalog, CONTROL, "setup", step(primitive, **WORKERS))
    assert found(catalog) == []


# load


def load_findings(root: Path, **changes: Any) -> list[tuple[str, str, str]]:
    """The findings with two valid paths, the first one's step changed by CHANGES.

    A change to None leaves the parameter out.
    """
    params = {key: value for key, value in {**WORKERS, **changes}.items() if value is not None}
    add(root, ONE, "inject", step("proc.cpu_hog", **params))
    add(root, TWO, "inject", step("proc.cpu_hog", **PARALLEL))
    return found(root)


ONE_PATH = (
    "scenarios",
    "paths",
    "in the split dev, proc.cpu_hog reaches 1 component(s) on 1 blueprint(s): it must reach at"
    " least two components on at least two blueprints",
)


def load(problem: str) -> tuple[str, str, str]:
    return (ONE, "load", f"inject.2: proc.cpu_hog: {problem}")


@pytest.mark.usefixtures("profile")
@pytest.mark.parametrize(
    ("changes", "unnamed", "one_path"),
    [
        ({"node": None}, "node", False),
        ({"component": None}, "component", True),
        ({"setting": None}, "setting", False),
        ({"node": None, "component": None, "setting": None}, "node, component, setting", True),
        ({"node": "${which}"}, "node", False),
        ({"component": "report-${which}"}, "component", True),
        ({"setting": "${which}"}, "setting", False),
        ({"node": 1}, "node", False),
        ({"component": ["report-builder"]}, "component", True),
        ({"setting": {"name": "workers"}}, "setting", False),
    ],
)
def test_a_load_primitive_names_its_node_component_and_setting_outright(
    catalog: Path, changes: dict[str, Any], unnamed: str, one_path: bool
) -> None:
    choice = {"type": "choice", "values": ["web", "builder", "workers"]}
    edit_yaml(catalog, ONE, lambda d: d["parameters"].update(which=choice))
    assert load_findings(catalog, **changes) == [
        # A component that is not named outright is no path either.
        *([ONE_PATH] if one_path else []),
        load(f"it must name its {unnamed} outright, as text without a parameter"),
    ]


@pytest.mark.usefixtures("profile")
@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"node": "db"}, "its node is no node of the blueprint fixture-web"),
        ({"node": "Web"}, "its node is no node of the blueprint fixture-web"),
        ({"component": "nginx"}, "its component is no background component of the node web"),
        ({"setting": "parallel"}, "its setting is no setting of the component report-builder"),
        ({"setting": "Workers"}, "its setting is no setting of the component report-builder"),
        ({"setting": "a$$b"}, "its setting is no setting of the component report-builder"),
        ({"value": None}, "it must give a value: a string, a number or a boolean"),
        ({"value": [64]}, "it must give a value: a string, a number or a boolean"),
        ({"value": {"workers": 64}}, "it must give a value: a string, a number or a boolean"),
    ],
)
def test_a_load_primitive_names_what_the_blueprint_declares_and_gives_a_value(
    catalog: Path, changes: dict[str, Any], problem: str
) -> None:
    assert load_findings(catalog, **changes) == [load(problem)]


def test_a_component_of_another_blueprint_is_no_component_of_the_node(
    catalog: Path, profile: None
) -> None:
    # The name is the other path's too: one component name is one component.
    assert load_findings(catalog, component="ledger-compactor") == [
        reaches("proc.cpu_hog", 1, 2),
        load("its component is no background component of the node web"),
    ]


def test_a_missing_value_and_a_null_value_are_the_same_finding(
    catalog: Path, profile: None
) -> None:
    add(catalog, TWO, "inject", step("proc.cpu_hog", **PARALLEL))
    edit_yaml(
        catalog,
        ONE,
        lambda d: d["inject"].append(step("proc.cpu_hog", **{**WORKERS, "value": None})),
    )
    assert found(catalog) == [load("it must give a value: a string, a number or a boolean")]


def with_settings(root: Path, **settings: Any) -> None:
    """Give the web's background component the settings SETTINGS beside its own."""
    edit_yaml(root, WEB, lambda d: d["nodes"][0]["components"][0]["settings"].update(settings))


IS_NORMAL = "its value is the normal value of"
MAY_BE_NORMAL = "its value is, in an instance, the normal value of"


@pytest.mark.usefixtures("profile")
@pytest.mark.parametrize(
    ("setting", "value", "normal"),
    [
        # workers: 1, batch_size: 50, mode: steady, ratio: 0.5, verbose: false, level: "5"
        ("workers", 1, True),
        ("workers", 1.0, True),
        ("workers", "1", True),
        ("workers", 2, False),
        ("workers", True, False),
        ("workers", "1.0", True),
        ("workers", "01", True),
        ("workers", " 1", True),
        ("workers", "1 ", True),
        ("workers", "+1", True),
        ("workers", "1e0", True),
        ("workers", "1.", True),
        ("workers", "10e-1", True),
        ("workers", "0x1", False),
        ("workers", "1,0", False),
        ("workers", "1 0", False),
        ("workers", "one", False),
        ("workers", "", False),
        ("workers", "1.5", False),
        ("workers", "-1", False),
        ("batch_size", 50, True),
        ("batch_size", 5e1, True),
        ("batch_size", "50", True),
        ("batch_size", 500, False),
        ("mode", "steady", True),
        ("mode", "Steady", False),
        ("mode", "busy", False),
        ("mode", 1, False),
        ("ratio", 0.5, True),
        ("ratio", "0.5", True),
        ("ratio", 0.25, False),
        ("verbose", False, True),
        ("verbose", "false", True),
        ("verbose", True, False),
        ("verbose", 0, False),
        ("level", "5", True),
        ("level", 5, True),
        ("level", 5.0, True),
        ("level", "05", True),
        ("level", "5.0", True),
        ("level", "5.5", False),
        ("ratio", ".5", True),
        ("ratio", "0.50", True),
        ("ratio", "5e-1", True),
        ("ratio", "1/2", False),
        ("mode", " steady", False),
        ("level", 6, False),
        ("price", "a$$b", True),
        ("price", "a$$$$b", False),
    ],
)
def test_a_load_primitive_s_value_is_not_the_setting_s_normal_value(
    catalog: Path, setting: str, value: Any, normal: bool
) -> None:
    with_settings(catalog, mode="steady", ratio=0.5, verbose=False, level="5", price="a$b")
    assert load_findings(catalog, setting=setting, value=value) == (
        [load(f"{IS_NORMAL} {setting}")] if normal else []
    )


CHOICE = "choice"


def parameter(*values: Any, low: int | None = None, high: int = 0, by: int = 1) -> dict[str, Any]:
    if low is None:
        return {"type": CHOICE, "values": list(values)}
    return {"type": "range", "min": low, "max": high, "step": by}


@pytest.mark.usefixtures("profile")
@pytest.mark.parametrize(
    ("setting", "value", "parameters", "normal"),
    [
        # One placeholder: the parameter's values themselves.
        ("workers", "${n}", {"n": parameter(low=1, high=8)}, True),
        ("workers", "${n}", {"n": parameter(low=2, high=8)}, False),
        ("workers", "${n}", {"n": parameter(low=-3, high=9, by=4)}, True),
        ("workers", "${n}", {"n": parameter(low=0, high=8, by=2)}, False),
        ("workers", "${n}", {"n": parameter(low=-(2**53) + 1, high=2**53 - 1)}, True),
        ("workers", "${n}", {"n": parameter(low=2, high=2**53 - 1)}, False),
        ("workers", "${n}", {"n": parameter(8, 4, 1)}, True),
        ("workers", "${n}", {"n": parameter(8, 4, "1")}, True),
        ("workers", "${n}", {"n": parameter(8, 4, 1.0)}, True),
        ("workers", "${n}", {"n": parameter(8, 4, True)}, False),
        ("workers", "${n}", {"n": parameter(8, 4)}, False),
        ("level", "${n}", {"n": parameter(low=0, high=9)}, True),
        ("level", "${n}", {"n": parameter(low=6, high=9)}, False),
        ("padded", "${n}", {"n": parameter(low=0, high=9)}, True),
        ("padded", "${n}", {"n": parameter(low=6, high=9)}, False),
        ("minus", "${n}", {"n": parameter(low=-1, high=1)}, True),
        ("minus", "${n}", {"n": parameter(low=1, high=2)}, False),
        ("huge", "${n}", {"n": parameter(low=0, high=2**53 - 1)}, False),
        ("batch_size", "${n}.0", {"n": parameter(40, 50)}, True),
        ("batch_size", "${n}.5", {"n": parameter(40, 50)}, False),
        ("workers", "0${n}", {"n": parameter(low=1, high=3)}, True),
        ("workers", "0${n}", {"n": parameter(low=2, high=3)}, False),
        ("ratio", "${n}", {"n": parameter(low=0, high=1)}, False),
        ("whole", "${n}", {"n": parameter(low=0, high=9)}, True),
        ("verbose", "${n}", {"n": parameter(low=0, high=1)}, False),
        ("verbose", "${n}", {"n": parameter(True, False)}, True),
        ("mode", "${n}", {"n": parameter("busy", "steady")}, True),
        ("mode", "${n}", {"n": parameter("busy", "spinning")}, False),
        # Text around the parameters: every instance's value is that text.
        ("mode", "st${n}", {"n": parameter("orm", "eady")}, True),
        ("mode", "st${n}", {"n": parameter("orm", "eadily")}, False),
        ("batch_size", "${n}0", {"n": parameter(low=1, high=9)}, True),
        ("batch_size", "${n}0", {"n": parameter(low=6, high=9)}, False),
        ("batch_size", "${n}${m}", {"n": parameter(4, 5), "m": parameter(low=0, high=1)}, True),
        ("batch_size", "${n}${m}", {"n": parameter(4, 6), "m": parameter(low=0, high=1)}, False),
        ("batch_size", "${n}${n}", {"n": parameter(low=0, high=9)}, False),
        ("level", "${n}${n}", {"n": parameter("", "5")}, False),
        ("price", "a$$${n}", {"n": parameter("b", "c")}, True),
        ("verbose", "f${n}", {"n": parameter("alse", "ull")}, True),
        (
            "batch_size",
            "${n}${m}",
            {"n": parameter(low=0, high=99), "m": parameter(low=0, high=99)},
            True,
        ),
    ],
)
def test_no_instance_s_value_is_the_setting_s_normal_value(
    catalog: Path, setting: str, value: str, parameters: dict[str, Any], normal: bool
) -> None:
    with_settings(
        catalog,
        mode="steady",
        ratio=0.5,
        verbose=False,
        level="5",
        price="a$b",
        padded="05",
        minus="-0",
        whole=5.0,
        # Written with a point and a signed exponent, so that it stays a text in the file.
        huge="1.0e+999999999",
    )
    edit_yaml(catalog, ONE, lambda d: d["parameters"].update(parameters))
    assert load_findings(catalog, setting=setting, value=value) == (
        [load(f"{MAY_BE_NORMAL} {setting}")] if normal else []
    )


def test_too_many_instances_to_compare_is_a_finding(catalog: Path, profile: None) -> None:
    def ranges(top: int) -> dict[str, Any]:
        return {"n": parameter(low=100, high=199), "m": parameter(low=0, high=top)}

    assert lint_module.MAX_COMBINATIONS == 10_000
    edit_yaml(catalog, ONE, lambda d: d["parameters"].update(ranges(100)))
    assert load_findings(catalog, setting="batch_size", value="${n}-${m}") == [
        load(
            "its value has more than 10000 combinations of parameter values, too many to compare"
            " with the normal value of batch_size"
        )
    ]
    # One parameter alone is never too many, and neither are exactly ten thousand.
    edit_yaml(catalog, ONE, lambda d: d["parameters"].update(ranges(99)))
    edit_yaml(catalog, ONE, lambda d: d["inject"][2]["params"].update(value="${n}"))
    assert found(catalog) == []
    edit_yaml(catalog, ONE, lambda d: d["inject"][2]["params"].update(value="${n}-${m}"))
    assert found(catalog) == []


def test_a_load_primitive_in_setup_and_in_a_control_is_checked_too(
    catalog: Path, profile: None
) -> None:
    two_paths(catalog)
    add(catalog, TWO, "setup", step("proc.mem_hog", **{**PARALLEL, "value": 1}))
    add(catalog, CONTROL, "setup", step("proc.fd_exhaust", **{**WORKERS, "setting": "threads"}))
    assert found(catalog) == [
        (
            "scenarios",
            "paths",
            "in the split dev, proc.mem_hog reaches 1 component(s) on 1 blueprint(s): it must"
            " reach at least two components on at least two blueprints",
        ),
        (TWO, "load", f"setup.0: proc.mem_hog: {IS_NORMAL} parallel"),
        (
            CONTROL,
            "load",
            "setup.0: proc.fd_exhaust: its setting is no setting of the component report-builder",
        ),
    ]


def test_a_scenario_without_its_blueprint_has_that_finding_only(
    catalog: Path, profile: None
) -> None:
    two_paths(catalog)
    edit_yaml(catalog, ONE, lambda d: d.update(blueprint="gone"))
    assert found(catalog) == [(ONE, "blueprint", "the blueprint gone is not in blueprints/")]


def test_another_primitive_s_parameters_are_not_a_load_primitive_s(
    catalog: Path, profile: None
) -> None:
    add(catalog, ONE, "inject", step("file.mutate", node="db", component="x", setting=1))
    assert found(catalog) == []


# paths


def reaches(primitive: str, components: int, blueprints: int, split: str = "dev") -> Any:
    return (
        "scenarios",
        "paths",
        f"in the split {split}, {primitive} reaches {components} component(s) on {blueprints}"
        " blueprint(s): it must reach at least two components on at least two blueprints",
    )


def new_scenario(root: Path, source: str, scenario_id: str, **changes: Any) -> str:
    """Copy the scenario SOURCE as SCENARIO_ID, with CHANGES; return its scenario.yaml."""
    shutil.copytree(root / "scenarios" / source, root / "scenarios" / scenario_id)
    path = f"scenarios/{scenario_id}/1/scenario.yaml"
    edit_yaml(root, path, lambda d: d.update(id=scenario_id, **changes))
    return path


@pytest.mark.usefixtures("profile")
def test_a_load_primitive_reaches_two_components_on_two_blueprints(catalog: Path) -> None:
    add(catalog, ONE, "inject", step("proc.cpu_hog", **WORKERS))
    assert found(catalog) == [reaches("proc.cpu_hog", 1, 1)]
    add(catalog, TWO, "inject", step("proc.cpu_hog", **PARALLEL))
    assert found(catalog) == []


@pytest.mark.usefixtures("profile")
def test_one_component_name_on_two_blueprints_is_one_component(catalog: Path) -> None:
    def renamed(d: Any) -> None:
        d["nodes"][0]["components"][0]["name"] = "report-builder"

    edit_yaml(catalog, STORE, renamed)
    add(catalog, ONE, "inject", step("proc.cpu_hog", **WORKERS))
    add(catalog, TWO, "inject", step("proc.cpu_hog", **{**PARALLEL, "component": "report-builder"}))
    assert found(catalog) == [reaches("proc.cpu_hog", 1, 2)]


@pytest.mark.usefixtures("profile")
def test_two_components_on_one_blueprint_are_one_blueprint(catalog: Path) -> None:
    def second(d: Any) -> None:
        component = {
            "name": "cache-warmer",
            "settings_file": "/etc/fixture-app/cache-warmer.conf",
            "settings": {"threads": 1},
        }
        d["nodes"][0]["components"].append(component)

    edit_yaml(catalog, WEB, second)
    other = new_scenario(catalog, "FIXTURE-001", "FIXTURE-003")
    add(catalog, ONE, "inject", step("proc.cpu_hog", **WORKERS))
    warmer = {"node": "web", "component": "cache-warmer", "setting": "threads", "value": 9}
    add(catalog, other, "inject", step("proc.cpu_hog", **warmer))
    assert found(catalog) == [reaches("proc.cpu_hog", 2, 1)]
    # The same two steps in one scenario are the same two paths.
    edit_yaml(catalog, other, lambda d: d["inject"].pop())
    add(catalog, ONE, "setup", step("proc.cpu_hog", **warmer))
    assert found(catalog) == [reaches("proc.cpu_hog", 2, 1)]
    add(catalog, TWO, "inject", step("proc.cpu_hog", **PARALLEL))
    assert found(catalog) == []


@pytest.mark.usefixtures("profile")
def test_the_paths_are_counted_in_each_split_and_for_each_primitive(catalog: Path) -> None:
    two_paths(catalog)
    add(catalog, ONE, "inject", step("proc.mem_hog", **WORKERS))
    assert found(catalog) == [reaches("proc.mem_hog", 1, 1)]
    add(catalog, TWO, "inject", step("proc.mem_hog", **PARALLEL))
    assert found(catalog) == []
    edit_yaml(catalog, TWO, lambda d: d.update(split="test"))
    assert found(catalog) == [
        reaches("proc.cpu_hog", 1, 1),
        reaches("proc.mem_hog", 1, 1),
        reaches("proc.cpu_hog", 1, 1, "test"),
        reaches("proc.mem_hog", 1, 1, "test"),
    ]


@pytest.mark.usefixtures("profile")
def test_a_scenario_counts_as_its_newest_version_has_it(catalog: Path) -> None:
    # Version 1 on one blueprint and version 2 on another are not two paths.
    add(catalog, ONE, "inject", step("proc.cpu_hog", **WORKERS))
    shutil.copytree(catalog / "scenarios/FIXTURE-001/1", catalog / "scenarios/FIXTURE-001/2")
    two = "scenarios/FIXTURE-001/2/scenario.yaml"

    def moved(d: Any) -> None:
        d.update(version=2, blueprint="fixture-store")
        d["inject"][2]["params"] = dict(PARALLEL)

    edit_yaml(catalog, two, moved)
    assert found(catalog) == [reaches("proc.cpu_hog", 1, 1)]
    add(catalog, TWO, "inject", step("proc.cpu_hog", **PARALLEL))
    assert found(catalog) == [reaches("proc.cpu_hog", 1, 1)]
    edit_yaml(catalog, two, lambda d: d.update(blueprint="fixture-web"))
    edit_yaml(catalog, two, lambda d: d["inject"][2].update(params=dict(WORKERS)))
    assert found(catalog) == []


@pytest.mark.usefixtures("profile")
def test_a_control_s_steps_are_no_paths(catalog: Path) -> None:
    add(catalog, TWO, "inject", step("proc.cpu_hog", **PARALLEL))
    add(catalog, CONTROL, "setup", step("proc.cpu_hog", **WORKERS))
    assert found(catalog) == [reaches("proc.cpu_hog", 1, 1)]
    edit_yaml(catalog, CONTROL, lambda d: d["setup"].pop())
    assert found(catalog) == [reaches("proc.cpu_hog", 1, 1)]
    edit_yaml(catalog, TWO, lambda d: d["inject"].pop())
    add(catalog, CONTROL, "setup", step("proc.cpu_hog", **WORKERS))
    assert found(catalog) == []


def test_the_paths_are_counted_while_the_primitives_are_unavailable_too(catalog: Path) -> None:
    add(catalog, ONE, "inject", step("proc.cpu_hog", **WORKERS))
    assert [rule for _, rule, _ in found(catalog)] == ["paths", "available"]


# ticket


def ticket(root: Path, text: str, scenario: str = ONE, **parameters: Any) -> list[str]:
    """The ticket findings' messages after SCENARIO's ticket becomes TEXT."""

    def change(d: Any) -> None:
        d["ticket"] = text
        d["parameters"].update(parameters)

    edit_yaml(root, scenario, change)
    findings = found(root)
    assert all((path, rule) == (scenario, "ticket") for path, rule, _ in findings)
    return [message for _, _, message in findings]


@pytest.mark.parametrize(
    ("text", "held"),
    [
        ("See FIXTURE-001 for the details.", ["the scenario ID FIXTURE-001"]),
        ("See FIXTURE-002 for the details.", ["the scenario ID FIXTURE-002"]),
        ("as in fixture-control-000.", ["the scenario ID FIXTURE-CONTROL-000"]),
        ("(Fixture-002)", ["the scenario ID FIXTURE-002"]),
        (
            "FIXTURE-002-b and FIXTURE-001/x",
            ["the scenario ID FIXTURE-001", "the scenario ID FIXTURE-002"],
        ),
        ("FIXTURE-0021 xFIXTURE-002 FIXTURE-002_b 1FIXTURE-002 FIXTURE-002é", []),
        ("FIXTURE-003 and FIXTURE-CONTROL-001 are no scenarios here.", []),
        ("Somebody ran disk.fill on it.", ["the fault primitive name disk.fill"]),
        (
            "Somebody ran Disk.Fill, or NGINX.CORRUPT_CONF.",
            [
                "the fault primitive name disk.fill",
                "the fault primitive name nginx.corrupt_conf",
            ],
        ),
        ("The disk.filled state, disk_fill, disk fill and mydisk.fill are no names.", []),
        ("It is a config.syntax_error.", ["the label config.syntax_error of its ground truth"]),
        ("It is a Config.Syntax_Error!", ["the label config.syntax_error of its ground truth"]),
        ("A config.syntax_errors list, a config syntax error, my_config.syntax_error.", []),
        ("The disk is full: resource.disk_full, or service.stopped.", []),
        ("Is none of this unknown to you? None? Unknown?", []),
        ("Ask the pokus team.", ["the string pokus"]),
        (
            "See /opt/Potka/bin and the pinkadmin, POKUSPOTKA.",
            [
                "the string pinka",
                "the string pokus",
                "the string potka",
            ],
        ),
        ("The web server and its location block are broken.", []),
    ],
)
def test_a_ticket_holds_no_scenario_id_primitive_name_own_label_or_lab_name(
    catalog: Path, text: str, held: list[str]
) -> None:
    assert ticket(catalog, text) == [f"the ticket holds {what}" for what in held]


def test_a_ticket_s_labels_are_its_own_ground_truth_s(catalog: Path) -> None:
    assert ticket(catalog, "It is resource.disk_full, not config.syntax_error.", TWO) == [
        "the ticket holds the label resource.disk_full of its ground truth"
    ]
    assert ticket(catalog, "The import failed.", TWO) == []

    def accepted(d: Any) -> None:
        d["diagnosis"]["accepted_labels"] += ["config.wrong_value", "unknown"]

    edit_yaml(catalog, "scenarios/FIXTURE-001/1/ground_truth.yaml", accepted)
    text = "A config.wrong_value, a service.stopped or something unknown."
    assert ticket(catalog, text) == [
        "the ticket holds the label config.wrong_value of its ground truth"
    ]
    assert ticket(catalog, "The pages do not load.") == []
    assert ticket(catalog, "There is none; nothing is unknown.", CONTROL) == []


@pytest.mark.parametrize(
    ("text", "parameters", "held"),
    [
        (
            "The ${what} pages.",
            {"what": parameter("shop", "FIXTURE-002", "help")},
            ["the scenario ID FIXTURE-002"],
        ),
        ("The ${what} pages.", {"what": parameter("potka", "shop")}, ["the string potka"]),
        (
            "The ${what} pages.",
            {"what": parameter("shop", "help", "my pinka")},
            ["the string pinka"],
        ),
        (
            "Somebody ran disk.${what}.",
            {"what": parameter("check", "fill")},
            ["the fault primitive name disk.fill"],
        ),
        (
            "A ${a}.${b}.",
            {"a": parameter("x", "config"), "b": parameter("y", "syntax_error")},
            ["the label config.syntax_error of its ground truth"],
        ),
        ("A ${a}.${b}.", {"a": parameter("x", "config"), "b": parameter("y", "syntax_errors")}, []),
        # A range is read at its smallest value only.
        ("See FIXTURE-00${n}.", {"n": parameter(low=0, high=9)}, []),
        ("See FIXTURE-00${n}.", {"n": parameter(low=2, high=9)}, ["the scenario ID FIXTURE-002"]),
        (
            "See FIXTURE-00${n}.",
            {"n": parameter(low=1, high=9, by=1)},
            ["the scenario ID FIXTURE-001"],
        ),
        ("See FIXTURE-00${n}.", {"n": parameter(low=3, high=9)}, []),
        ("See FIXTURE-002${what}.", {"what": parameter("a", "b")}, ["the scenario ID FIXTURE-002"]),
        ("It costs $$5, po$$tka, ${what}.", {"what": parameter(True, 1.5, "x")}, []),
        # A parameter's name is no text of the ticket; its values are.
        ("Port ${potka_port} is closed.", {"potka_port": parameter(80, 81)}, []),
        (
            "See ${fixture_002} and ${disk}.${fill}.",
            {
                "fixture_002": parameter("a", "b"),
                "disk": parameter("x"),
                "fill": parameter("y"),
            },
            [],
        ),
        (
            "Port ${potka_port} is closed.",
            {"potka_port": parameter(80, "potka")},
            ["the string potka"],
        ),
    ],
)
def test_a_ticket_is_read_as_written_and_with_every_value_of_its_choices(
    catalog: Path, text: str, parameters: dict[str, Any], held: list[str]
) -> None:
    assert ticket(catalog, text, ONE, **parameters) == [f"the ticket holds {what}" for what in held]


def test_a_ticket_with_too_many_combinations_to_read_is_a_finding(catalog: Path) -> None:
    hundred = [f"v{number}" for number in range(100)]
    parameters = {
        "a": parameter(*hundred),
        "b": parameter(*hundred),
        "c": parameter(low=0, high=10**9),
    }
    assert ticket(catalog, "The ${a} and ${b} pages, order ${c}.", ONE, **parameters) == []
    edit_yaml(catalog, ONE, lambda d: d["parameters"].update(b=parameter(*hundred, "v100")))
    assert found(catalog) == [
        (
            ONE,
            "ticket",
            "the ticket has more than 10000 combinations of the values of its choices, too many"
            " to read",
        )
    ]
    # Too many to read as instances, it is still read as it is written.
    assert sorted(ticket(catalog, "See FIXTURE-002: ${a} and ${b} pages, order ${c}.")) == [
        "the ticket has more than 10000 combinations of the values of its choices, too many"
        " to read",
        "the ticket holds the scenario ID FIXTURE-002",
    ]
    assert ticket(catalog, "The ${a} pages, order ${c}, potka.") == [
        "the ticket holds the string potka"
    ]


def test_a_parameter_the_ticket_does_not_use_is_not_read(catalog: Path) -> None:
    assert ticket(catalog, "The pages are slow.", unused=parameter("potka", "FIXTURE-002")) == []


# leakage


def write(root: Path, name: str, content: str | bytes = "x\n") -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return name


def test_an_overlay_has_no_git_entry(catalog: Path) -> None:
    config = write(catalog, f"{WEB_OVERLAY}/files/opt/app/.git/config")
    gitlink = write(catalog, f"{STORE_OVERLAY}/files/.git")
    upper = write(catalog, f"{WEB_OVERLAY}/files/opt/app/.GIT/HEAD")
    mixed = write(catalog, f"{WEB_OVERLAY}/files/opt/tool/.Git")
    write(catalog, f"{WEB_OVERLAY}/files/opt/app/.gitignore")
    write(catalog, f"{WEB_OVERLAY}/files/opt/app/x.git/config")
    write(catalog, f"{WEB_OVERLAY}/files/opt/app/.github/x")
    write(catalog, f"{WEB_OVERLAY}/files/opt/app/git/x")
    assert found(catalog) == [
        (gitlink, "leakage", "its path has a .git component"),
        (upper, "leakage", "its path has a .git component"),
        (config, "leakage", "its path has a .git component"),
        (mixed, "leakage", "its path has a .git component"),
    ]


@pytest.mark.parametrize(
    ("name", "held"),
    [
        ("files/etc/potka/app.conf", ["the string potka"]),
        ("files/opt/Pokus-tools/x", ["the string pokus"]),
        ("files/opt/app/pinka.py", ["the string pinka"]),
        ("files/opt/fixture-002/x", ["the scenario ID FIXTURE-002"]),
        ("files/opt/FIXTURE-CONTROL-000.txt", ["the scenario ID FIXTURE-CONTROL-000"]),
        ("files/opt/fixture-0021/x", []),
        ("files/usr/local/bin/disk.fill", ["the fault primitive name disk.fill"]),
        ("files/usr/local/bin/disk.fill.sh", ["the fault primitive name disk.fill"]),
        ("files/usr/local/bin/disk.filler", []),
        ("files/etc/accepted_labels", ["the ground truths' field name accepted_labels"]),
        ("files/etc/my_then_required.d/x", ["the ground truths' field name then_required"]),
        ("files/etc/Resolution_State", ["the ground truths' field name resolution_state"]),
        (
            "files/etc/config.syntax_error",
            ["the label config.syntax_error, which a ground truth uses"],
        ),
        (
            "files/etc/resource.disk_full.d/x",
            ["the label resource.disk_full, which a ground truth uses"],
        ),
        ("files/etc/service.stopped", []),
        ("files/etc/none/unknown", []),
        ("files/etc/location/component/category/actions/fault", []),
        ("files/etc/the web server", []),
    ],
)
def test_an_overlay_s_paths_hold_no_forbidden_term(
    catalog: Path, name: str, held: list[str]
) -> None:
    path = write(catalog, f"{WEB_OVERLAY}/{name}")
    assert found(catalog) == [(path, "leakage", f"its path holds {what}") for what in held]


@pytest.mark.parametrize(
    ("content", "held"),
    [
        ("# part of potka\n", [(1, "the string potka")]),
        ("a\nb\nPOKUS_HOME=/x\n", [(3, "the string pokus")]),
        ("a\n\nmypinkas\npinka\n", [(3, "the string pinka")]),
        ("# for FIXTURE-001\n", [(1, "the scenario ID FIXTURE-001")]),
        ("x\n# for fixture-001, FIXTURE-0010\n", [(2, "the scenario ID FIXTURE-001")]),
        ("FIXTURE-0010 xFIXTURE-001 FIXTURE-001_x\n", []),
        ("out.write(x)\nfile.write(x)\n", [(2, "the fault primitive name file.write")]),
        ("self.file.write(x)\n", [(1, "the fault primitive name file.write")]),
        ("myfile.write(x)\nfile.writelines(x)\nfile_write(x)\n", []),
        ("x = proc.cpu_hog\n", [(1, "the fault primitive name proc.cpu_hog")]),
        ("ACCEPTED_LABELS = 1\n", [(1, "the ground truths' field name accepted_labels")]),
        ("\nmy_then_required_x\n", [(2, "the ground truths' field name then_required")]),
        ("resolution_state\n", [(1, "the ground truths' field name resolution_state")]),
        ("accepted labels, then-required, resolution state\n", []),
        ("location /x {\n}\ncomponent category actions fault diagnosis\n", []),
        (
            "a\nb\nc\nconfig.syntax_error\n",
            [(4, "the label config.syntax_error, which a ground truth uses")],
        ),
        ("Resource.Disk_Full\n", [(1, "the label resource.disk_full, which a ground truth uses")]),
        ("service.stopped config.wrong_value resource.cpu_saturated\n", []),
        ("none unknown None UNKNOWN\n", []),
        ("the web server, the database node's temporary volume\n", []),
        (
            "potka\nFIXTURE-002\npotka FIXTURE-002\n",
            [(1, "the string potka"), (2, "the scenario ID FIXTURE-002")],
        ),
        (b"\x00\xff\xfe binary potka\x00\n\xff", [(1, "the string potka")]),
        (b"\xff\n\xfe\nFIXTURE-001\xff\n", [(3, "the scenario ID FIXTURE-001")]),
        ("FIXTURE-001é\n", []),
        ("", []),
    ],
)
def test_an_overlay_s_files_hold_no_forbidden_term(
    catalog: Path, content: str | bytes, held: list[tuple[int, str]]
) -> None:
    path = write(catalog, f"{WEB_OVERLAY}/files/opt/app/job.py", content)
    assert found(catalog) == [(path, "leakage", f"line {line} holds {what}") for line, what in held]
    install = f"{STORE_OVERLAY}/install.sh"
    script = (catalog / install).read_bytes()
    body = content if isinstance(content, bytes) else content.encode("utf-8")
    (catalog / install).write_bytes(script + body)
    lines = script.count(b"\n")
    assert found(catalog) == sorted(
        [(path, "leakage", f"line {line} holds {what}") for line, what in held]
        + [(install, "leakage", f"line {lines + line} holds {what}") for line, what in held]
    )


def test_a_term_in_a_path_and_in_the_file_are_two_findings(catalog: Path) -> None:
    path = write(catalog, f"{WEB_OVERLAY}/files/opt/potka/.git/HEAD", "ref: potka\n")
    assert found(catalog) == [
        (path, "leakage", "its path has a .git component"),
        (path, "leakage", "its path holds the string potka"),
        (path, "leakage", "line 1 holds the string potka"),
    ]


def test_an_overlay_no_node_names_is_read_too(catalog: Path) -> None:
    path = write(catalog, "blueprints/spare-app/overlay/files/etc/x", "pinka\n")
    assert found(catalog) == [
        ("blueprints/spare-app/overlay", "overlay", "no node names this derived image"),
        (path, "leakage", "line 1 holds the string pinka"),
    ]


def test_the_overlay_s_own_directory_is_not_its_content(catalog: Path) -> None:
    # A derived image's name is no file of the image.
    def renamed(d: Any) -> None:
        d["nodes"][0]["derived"]["name"] = "potka-web-app"

    shutil.move(catalog / "blueprints/fixture-web-app", catalog / "blueprints/potka-web-app")
    edit_yaml(catalog, WEB, renamed)
    assert found(catalog) == []


def test_a_file_that_cannot_be_read_is_a_finding(
    catalog: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    read_bytes = Path.read_bytes

    def refuse(self: Path) -> bytes:
        if self.name == "report-builder":
            raise PermissionError(13, "Permission denied", str(self))
        return read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", refuse)
    assert found(catalog) == [
        (
            f"{WEB_OVERLAY}/files/usr/local/bin/report-builder",
            "leakage",
            "cannot be read (PermissionError)",
        )
    ]


def test_the_terms_follow_the_catalog(catalog: Path) -> None:
    path = write(catalog, f"{WEB_OVERLAY}/files/etc/x", "FIXTURE-003 service.stopped\n")
    assert found(catalog) == []
    new_scenario(catalog, "FIXTURE-002", "FIXTURE-003")

    def stopped(d: Any) -> None:
        d["diagnosis"]["accepted_labels"].append("service.stopped")

    edit_yaml(catalog, "scenarios/FIXTURE-003/1/ground_truth.yaml", stopped)
    assert found(catalog) == [
        (path, "leakage", "line 1 holds the label service.stopped, which a ground truth uses"),
        (path, "leakage", "line 1 holds the scenario ID FIXTURE-003"),
    ]


def test_without_a_table_given_the_release_s_must_be_readable(
    catalog: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing() -> GoldenImages:
        raise FileNotFoundError(2, "No such file or directory", "golden-images.json")

    monkeypatch.setattr(lint_module, "load_table", missing)
    assert lint(catalog, table()) == []
    with pytest.raises(FileNotFoundError):
        lint(catalog)
    with pytest.raises(FileNotFoundError):
        lint(catalog / "no-catalog")
