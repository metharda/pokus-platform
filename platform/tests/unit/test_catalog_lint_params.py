"""The catalog lint's rule on a step's parameters, and the view of the library it is given."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from potka_cli.main import library_views, step_views
from potka_core.catalog.lint import MAX_COMBINATIONS, Library, StepView, lint
from potka_primitives import library
from potka_primitives.base import Effects, Facts, Params, Primitive, Probe, Requires, Verdict
from potka_primitives.context import Node, Targets
from potka_primitives.values import AbsolutePath, Percent
from potka_testing.fixture_catalog import copy_fixture, edit_yaml
from potka_testing.tool_views import TOOLS
from pydantic import JsonValue, model_validator

ONE = "scenarios/FIXTURE-001/1/scenario.yaml"
TWO = "scenarios/FIXTURE-002/1/scenario.yaml"
TRUTH = "scenarios/FIXTURE-001/1/ground_truth.yaml"
STORE = "blueprints/fixture-store.yaml"
MARK = "zq-marked-value"


class CorruptParams(Params):
    path: AbsolutePath
    defect: str
    percent: Percent = 50

    @model_validator(mode="after")
    def _known(self) -> CorruptParams:
        if self.defect not in ("missing_semicolon", "unclosed_block", "unknown_directive"):
            raise ValueError("a defect is one of the three this test knows")
        return self


class Corrupt(Primitive[CorruptParams]):
    """A stand-in for the primitive the fixture scenarios name, with a service's package."""

    name = "nginx.corrupt_conf"
    version = 1
    Params = CorruptParams
    requires = Requires(service="nginx")

    def describe(self, params: CorruptParams) -> str:
        return "Breaks a file."

    def side_effects(self, params: CorruptParams) -> Effects:
        return Effects(files=(params.path,))

    def _apply(self, node: Node, params: CorruptParams) -> Facts:
        return {}


class StateParams(Params):
    unit: str
    state: str


class State(Probe[StateParams]):
    """A stand-in for a probe the fixture scenarios name."""

    name = "unit_state"
    version = 1
    Params = StateParams

    def describe(self, params: StateParams) -> str:
        return "Reads a unit's state."

    def _measure(self, targets: Targets, params: StateParams) -> Verdict:
        return Verdict("passed")


VIEWS = step_views((Corrupt,), (State,))


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    return copy_fixture(tmp_path / "catalog")


def found(root: Path, views: Library = VIEWS) -> list[tuple[str, str, str]]:
    findings = lint(root, TOOLS, views)
    assert findings == sorted(set(findings))
    return [(finding.path, finding.rule, finding.message) for finding in findings]


def inject(root: Path, *steps: dict[str, Any], name: str = ONE) -> None:
    edit_yaml(root, name, lambda data: data.update({"inject": list(steps)}))


def test_the_fixture_catalog_fits_and_a_name_the_library_has_not_is_not_checked(
    catalog: Path,
) -> None:
    assert found(catalog) == []
    # Without a view of a step nothing is said of its parameters, whatever they are.
    inject(catalog, {"primitive": "nginx.corrupt_conf", "params": {"anything": MARK}})
    assert found(catalog, Library({}, {})) == []
    assert [rule for _, rule, _ in found(catalog)] == ["params"]


def test_the_release_s_library_is_what_the_command_gives_the_lint() -> None:
    views = library_views()
    assert set(views.primitives) == set(library.PRIMITIVES)
    assert set(views.probes) == set(library.PROBES)
    assert VIEWS.primitives["nginx.corrupt_conf"].service == "nginx"
    assert VIEWS.probes["unit_state"].service is None


def test_a_step_s_parameters_fit_its_model_in_every_instance(catalog: Path) -> None:
    def defects(data: Any) -> None:
        data["parameters"]["defect"]["values"] = ["missing_semicolon", MARK, "unclosed_block"]

    edit_yaml(catalog, ONE, defects)
    # One of the three instances does not fit; the finding names the rule the model
    # gives and never the value.
    assert found(catalog) == [
        (
            ONE,
            "params",
            "inject.0: nginx.corrupt_conf: a defect is one of the three this test knows",
        )
    ]


@pytest.mark.parametrize(
    ("params", "said"),
    [
        ({"node": "web"}, "defect: is missing; path: is missing"),
        (
            {"node": "web", "path": "/etc/x", "defect": "unclosed_block", "more": MARK},
            "more: is no parameter of it",
        ),
        (
            {"node": "web", "path": "/etc/x", "defect": "unclosed_block", f"{MARK} key": 1},
            "(a key): is no parameter of it",
        ),
        (
            {"node": "web", "path": f"{MARK}/x", "defect": "unclosed_block"},
            "path: a path is absolute, in its normal form, of printable characters without a"
            " blank, and not / itself",
        ),
        (
            {"node": "web", "path": "/etc/x", "defect": 7},
            "defect: is not of the parameter's type",
        ),
        (
            {"node": "web", "path": "/etc/x", "defect": "unclosed_block", "percent": 0},
            "percent: is not within the parameter's bounds",
        ),
        (
            {"node": "web", "path": "/etc/x", "defect": "unclosed_block", "percent": "50"},
            "percent: is not of the parameter's type",
        ),
        (
            {"node": f"Web {MARK}", "path": "/etc/x", "defect": "unclosed_block"},
            "node: has not the parameter's form",
        ),
    ],
)
def test_a_finding_says_what_is_wrong_and_shows_no_value(
    catalog: Path, params: dict[str, Any], said: str
) -> None:
    inject(catalog, {"primitive": "nginx.corrupt_conf", "params": params})
    assert found(catalog) == [(ONE, "params", f"inject.0: nginx.corrupt_conf: {said}")]
    assert MARK not in str(found(catalog))


def test_a_step_s_node_is_a_node_of_the_blueprint_whose_image_carries_the_service(
    catalog: Path,
) -> None:
    fits = {"path": "/etc/nginx/nginx.conf", "defect": "unclosed_block"}
    inject(catalog, {"primitive": "nginx.corrupt_conf", "params": {"node": "db", **fits}})
    no_node = (
        ONE,
        "params",
        "inject.0: nginx.corrupt_conf: node: is no node of the scenario's blueprint",
    )
    assert found(catalog) == [no_node]
    # In the other scenario db is a node, of an image that has no nginx.
    inject(catalog, {"primitive": "nginx.corrupt_conf", "params": {"node": "db", **fits}}, name=TWO)
    assert found(catalog) == [
        no_node,
        (
            TWO,
            "params",
            "inject.0: nginx.corrupt_conf: the golden image of the node db does not carry"
            " nginx, the service it acts on",
        ),
    ]


def test_what_is_wrong_in_several_instances_is_said_once(catalog: Path) -> None:
    def change(data: Any) -> None:
        data["parameters"]["place"] = {"type": "choice", "values": ["/etc/nginx/a.conf", 7]}
        data["ticket"] = data["ticket"].rstrip("\n") + " Place ${place}.\n"
        data["inject"][0]["params"] = {"node": "web", "path": "${place}"}

    edit_yaml(catalog, ONE, change)
    # The defect is missing in both instances and the path is no text in one of them.
    assert found(catalog) == [
        (
            ONE,
            "params",
            "inject.0: nginx.corrupt_conf: defect: is missing; path: is not of the"
            " parameter's type",
        )
    ]


def test_what_another_rule_finds_is_not_said_again(catalog: Path) -> None:
    # A parameter the scenario does not declare, in a ground truth's probe.
    undeclared = {"node": "web", "unit": "${nowhere}", "state": "active"}
    edit_yaml(
        catalog,
        TRUTH,
        lambda data: data["collateral"].append({"probe": "unit_state", "params": undeclared}),
    )
    assert [rule for _, rule, _ in found(catalog)] == ["templates"]


def test_a_missing_blueprint_or_golden_image_is_another_rule_s_to_find(catalog: Path) -> None:
    edit_yaml(catalog, ONE, lambda data: data.update(blueprint="gone"))
    assert "params" not in [rule for _, rule, _ in found(catalog)]
    edit_yaml(catalog, ONE, lambda data: data.update(blueprint="fixture-web"))
    assert found(catalog) == []
    web = "blueprints/fixture-web.yaml"
    edit_yaml(catalog, web, lambda data: data["nodes"][0]["golden"].update(version="0.0.9"))
    rules = [rule for _, rule, _ in found(catalog)]
    assert "golden" in rules and "params" not in rules


def test_probes_are_checked_in_every_place_a_step_can_stand(catalog: Path) -> None:
    def wrong(node: str = "web") -> dict[str, Any]:
        return {"probe": "unit_state", "params": {"node": node, "unit": "nginx.service"}}

    def scenario(data: Any) -> None:
        data["preconditions"] = [wrong()]
        data["confirm"].append(wrong())
        data["setup"] = [{"primitive": "nginx.corrupt_conf", "params": {"node": "web"}}]

    def truth(data: Any) -> None:
        data["collateral"].append(wrong())
        data["verification"].append(wrong())

    edit_yaml(catalog, ONE, scenario)
    edit_yaml(catalog, TRUTH, truth)
    edit_yaml(catalog, STORE, lambda data: data["health"].append(wrong("db")))
    said = "unit_state: state: is missing"
    confirm = len(_yaml(catalog, ONE)["confirm"]) - 1
    collateral = len(_yaml(catalog, TRUTH)["collateral"]) - 1
    health = len(_yaml(catalog, STORE)["health"]) - 1
    verification = len(_yaml(catalog, TRUTH)["verification"]) - 1
    assert (confirm, collateral, health, verification) == (2, 1, 2, 3)
    assert found(catalog) == [
        (STORE, "params", f"health.{health}: {said}"),
        (TRUTH, "params", f"collateral.{collateral}: {said}"),
        (TRUTH, "params", f"verification.{verification}: {said}"),
        (ONE, "params", f"confirm.{confirm}: {said}"),
        (ONE, "params", f"preconditions.0: {said}"),
        (ONE, "params", "setup.0: nginx.corrupt_conf: defect: is missing; path: is missing"),
    ]
    # A health probe's node is a node of its own blueprint.
    edit_yaml(catalog, STORE, lambda data: data["health"][health]["params"].update(node="web"))
    assert found(catalog)[0] == (STORE, "params", f"health.{health}: {said}")
    edit_yaml(catalog, STORE, lambda data: data["health"][health]["params"].update(state="active"))
    assert found(catalog)[0] == (
        STORE,
        "params",
        f"health.{health}: unit_state: node: is no node of the blueprint",
    )


def _yaml(root: Path, name: str) -> Any:
    return yaml.safe_load((root / name).read_text(encoding="utf-8"))


def test_every_value_of_a_range_is_checked_up_to_the_bound(catalog: Path) -> None:
    def ranged(top: int, defect: str = "${defect}") -> None:
        def change(data: Any) -> None:
            data["parameters"]["level"] = {"type": "range", "min": 1, "max": top}
            data["ticket"] = data["ticket"].split(" Level ")[0].rstrip("\n") + " Level ${level}.\n"
            data["inject"][0]["params"].update(percent="${level}", defect=defect)

        edit_yaml(catalog, ONE, change)

    out_of_bounds = (
        ONE,
        "params",
        "inject.0: nginx.corrupt_conf: percent: is not within the parameter's bounds",
    )
    too_many = (
        ONE,
        "params",
        f"inject.0: nginx.corrupt_conf: its parameters have more than {MAX_COMBINATIONS}"
        " combinations of values: they cannot all be checked",
    )
    ranged(100)
    assert found(catalog) == []
    # Only the last value of the range does not fit, and it is found.
    ranged(101)
    assert found(catalog) == [out_of_bounds]
    # A step with exactly as many combinations as the bound is checked; one more is not.
    ranged(MAX_COMBINATIONS, "unclosed_block")
    assert found(catalog) == [out_of_bounds]
    ranged(MAX_COMBINATIONS + 1, "unclosed_block")
    assert found(catalog) == [too_many]
    # Three defects and a third of the bound in levels, and one level more.
    ranged(MAX_COMBINATIONS // 3)
    assert found(catalog) == [out_of_bounds]
    ranged(MAX_COMBINATIONS // 3 + 1)
    assert found(catalog) == [too_many]


def test_a_view_is_asked_with_the_parameters_of_each_instance(catalog: Path) -> None:
    seen: list[Mapping[str, JsonValue]] = []

    def problem(params: Mapping[str, JsonValue]) -> tuple[str, ...]:
        seen.append(dict(params))
        return ()

    views = Library({"nginx.corrupt_conf": StepView(problem, None)}, {})
    assert found(catalog, views) == []
    assert seen == [
        {"node": "web", "path": "/etc/nginx/nginx.conf", "defect": defect}
        for defect in ("missing_semicolon", "unclosed_block", "unknown_directive")
    ]
