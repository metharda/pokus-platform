"""The catalog lint's two rules on the tool registry: its labels, and a ground truth's actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from potka_cli.main import tool_views
from potka_core.catalog.lint import ToolView, lint
from potka_testing.fixture_catalog import copy_fixture, edit_yaml
from potka_testing.tool_views import TOOLS
from potka_testing.tools import forbidden, remove_file, restart_service, tail_journal, tool
from potka_tools.registry import Registry, load_registry

ONE = "scenarios/FIXTURE-001/1/ground_truth.yaml"
TWO = "scenarios/FIXTURE-002/1/ground_truth.yaml"


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    return copy_fixture(tmp_path / "catalog")


def found(root: Path, registry: dict[str, ToolView] | None = None) -> list[tuple[str, str, str]]:
    findings = lint(root, TOOLS if registry is None else registry)
    return [(finding.path, finding.rule, finding.message) for finding in findings]


def act(root: Path, name: str, kind: str, *actions: dict[str, Any]) -> None:
    """Make ACTIONS the actions of KIND in the ground truth NAME."""
    edit_yaml(root, name, lambda data: data["actions"].update({kind: list(actions)}))


def test_the_release_s_registry_is_what_the_lint_is_given(catalog: Path) -> None:
    assert found(catalog) == []
    registry = load_registry()
    assert set(TOOLS) == {one.name for one in registry.tools} and len(TOOLS) == 41
    restart, journal, reboot = TOOLS["restart_service"], TOOLS["tail_journal"], TOOLS["reboot"]
    assert (restart.mutating, restart.forbidden) == (True, False)
    assert (journal.mutating, journal.forbidden) == (False, False)
    assert (reboot.mutating, reboot.forbidden) == (True, True)
    assert restart.arguments == {"node", "unit"} and reboot.arguments == frozenset()
    written = registry.tool("restart_service")
    assert written is not None and restart.categories == written.applicable_categories
    assert journal.categories == ()


def test_a_label_a_tool_names_is_one_of_the_taxonomy(catalog: Path) -> None:
    made = Registry.of(
        1,
        (
            tool({**restart_service(), "applicable_categories": ["service.stopped", "made.up"]}),
            tool({**remove_file(), "applicable_categories": ["made.other", "made.up"]}),
            tool(tail_journal()),
            tool(forbidden()),
        ),
    )
    act(catalog, ONE, "acceptable")
    act(catalog, ONE, "then_required")
    act(catalog, ONE, "harmful")
    act(catalog, TWO, "harmful")
    act(catalog, "scenarios/FIXTURE-CONTROL-000/1/ground_truth.yaml", "harmful")
    assert found(catalog, tool_views(made)) == [
        (
            "taxonomy.yaml",
            "categories",
            "the tool remove_file names the label made.other, which is not in the taxonomy",
        ),
        (
            "taxonomy.yaml",
            "categories",
            "the tool remove_file names the label made.up, which is not in the taxonomy",
        ),
        (
            "taxonomy.yaml",
            "categories",
            "the tool restart_service names the label made.up, which is not in the taxonomy",
        ),
    ]
    # A label that leaves the taxonomy is missed by the tool that names it.
    edit_yaml(
        catalog,
        "taxonomy.yaml",
        lambda data: data.update(
            labels=[one for one in data["labels"] if one["name"] != "container.daemon_stopped"]
        ),
    )
    naming = [
        one.name
        for one in load_registry().tools
        if "container.daemon_stopped" in one.applicable_categories
    ]
    assert naming
    assert found(catalog) == [
        (
            "taxonomy.yaml",
            "categories",
            f"the tool {name} names the label container.daemon_stopped, which is not in the"
            " taxonomy",
        )
        for name in naming
    ]


def test_an_action_names_a_tool_that_changes_its_target_and_is_not_forbidden(
    catalog: Path,
) -> None:
    act(catalog, ONE, "acceptable", {"tool": "no_such_tool", "args": {"node": "web"}})
    act(catalog, ONE, "then_required", {"tool": "tail_journal", "args": {"node": "web"}})
    act(
        catalog,
        ONE,
        "harmful",
        {"tool": "stop_service", "args": {"node": "web"}},
        {"tool": "reboot", "args": {"anything": 1}},
    )
    assert found(catalog) == [
        (ONE, "actions", "acceptable.0: no_such_tool is no tool of the tool registry"),
        (
            ONE,
            "actions",
            "harmful.1: reboot is forbidden: an action is a call that changes its target",
        ),
        (
            ONE,
            "actions",
            "then_required.0: tail_journal is read-only: an action is a call that changes its"
            " target",
        ),
    ]


def test_an_action_s_arguments_are_the_tool_s_and_its_node_the_blueprint_s(catalog: Path) -> None:
    act(
        catalog,
        ONE,
        "acceptable",
        {"tool": "restart_service", "args": {"node": "db", "unit": "nginx.service"}},
        {"tool": "restart_service", "args": {"unit": "nginx.service", "lines": 5, "host": "web"}},
        {"tool": "restart_service", "args": {"a key; of another form": 1, "Unit": "x"}},
        {"tool": "restart_service", "args": {}},
    )
    unknown = [
        (ONE, "actions", "acceptable.1: restart_service has no argument host"),
        (ONE, "actions", "acceptable.1: restart_service has no argument lines"),
        # A key of another form than a name's is not shown.
        (ONE, "actions", "acceptable.2: restart_service has no argument of that name"),
    ]
    assert found(catalog) == [
        (
            ONE,
            "actions",
            "acceptable.0: restart_service node: is no node of the scenario's blueprint",
        ),
        *unknown,
    ]
    # Without its blueprint a scenario has that finding, and none of a node.
    scenario = "scenarios/FIXTURE-001/1/scenario.yaml"
    edit_yaml(catalog, scenario, lambda d: d.update(blueprint="no"))
    assert found(catalog) == [
        *unknown,
        (scenario, "blueprint", "the blueprint no is not in blueprints/"),
    ]


@pytest.mark.parametrize(
    ("tool_name", "args", "said"),
    [
        ("restart_service", {"unit": 5}, "unit: is not of the argument's type"),
        ("restart_service", {"unit": ["nginx.service"]}, "unit: is not of the argument's type"),
        ("restart_service", {"unit": True}, "unit: is not of the argument's type"),
        ("restart_service", {"unit": "nginx"}, "unit: has not the argument's form"),
        ("restart_service", {"unit": "nginx.service\n"}, "unit: has not the argument's form"),
        ("restart_service", {"node": 1}, "node: is not of the argument's type"),
        ("kill_process", {"pid": "1"}, "pid: is not of the argument's type"),
        ("kill_process", {"pid": 1.5}, "pid: is not of the argument's type"),
        ("kill_process", {"pid": 0}, "pid: is not within the argument's bounds"),
        ("kill_process", {"signal": "STOP"}, "signal: is not one of the argument's values"),
        # A value that is a whole element of the tool's command: no call can begin it with a dash.
        (
            "restart_service",
            {"unit": "-x.service"},
            "unit: begins with a dash where the tool's command takes a whole word",
        ),
        ("remove_file", {"path": "var/tmp/a"}, "path: has not the argument's form"),
        ("remove_file", {"path": "/etc/passwd"}, "path: is not below a root the tool allows"),
        ("remove_file", {"path": "/var/cache"}, "path: is not below a root the tool allows"),
        (
            "remove_file",
            {"path": "/var/cache/../../etc/a"},
            "path: is not below a root the tool allows",
        ),
        ("remove_file", {"path": "/var/cachex/a"}, "path: is not below a root the tool allows"),
    ],
)
def test_a_value_given_outright_fits_its_argument_s_own_rules(
    catalog: Path, tool_name: str, args: dict[str, Any], said: str
) -> None:
    act(catalog, TWO, "acceptable", {"tool": tool_name, "args": {"node": "db", **args}})
    assert found(catalog) == [(TWO, "actions", f"acceptable.0: {tool_name} {said}")]


def test_values_that_fit_and_values_through_a_parameter_are_no_finding(catalog: Path) -> None:
    act(
        catalog,
        TWO,
        "acceptable",
        {"tool": "remove_file", "args": {"node": "db", "path": "/var/cache/import.tmp"}},
        {"tool": "remove_file", "args": {"node": "db", "path": "/var/cache/./a/../import.tmp"}},
        {"tool": "kill_process", "args": {"node": "db", "pid": 4242, "signal": "KILL"}},
        {"tool": "restart_service", "args": {"node": "db", "unit": "postgresql.service"}},
        # What a parameter gives is not known here: neither its type nor its form.
        {"tool": "kill_process", "args": {"node": "db", "pid": "${percent}"}},
        {"tool": "remove_file", "args": {"node": "db", "path": "/etc/${percent}"}},
        {"tool": "restart_service", "args": {"node": "${percent}", "unit": "${percent}"}},
    )
    assert found(catalog) == []


def test_a_value_given_outright_is_read_as_a_call_would_give_it(catalog: Path) -> None:
    # A dollar sign written twice is one: the tool below takes one and not two.
    entry = restart_service()
    entry["args"]["unit"] = {"type": "string", "pattern": r"^a\$b\.service$"}
    views = {**TOOLS, **tool_views(Registry.of(1, (tool(entry),)))}
    act(catalog, TWO, "acceptable", {"tool": "restart_service", "args": {"unit": "a$$b.service"}})
    assert found(catalog, views) == []
    act(catalog, TWO, "acceptable", {"tool": "restart_service", "args": {"unit": "a$$$$b.service"}})
    assert found(catalog, views) == [
        (TWO, "actions", "acceptable.0: restart_service unit: has not the argument's form")
    ]


def test_no_finding_holds_the_value_it_is_about(catalog: Path) -> None:
    act(
        catalog,
        TWO,
        "acceptable",
        {"tool": "restart_service", "args": {"node": "db", "unit": "secret-unit-name"}},
        {"tool": "remove_file", "args": {"node": "db", "path": "/secret-place/file"}},
        {"tool": "stop_service", "args": {"node": "secretnode"}},
        {"tool": "stop_service", "args": {"secret key!": 1}},
    )
    messages = [message for _, _, message in found(catalog)]
    assert len(messages) == 4
    assert not any("secret" in message for message in messages)
    # The view says of a name the tool has not that it is none, and raises nothing.
    assert TOOLS["stop_service"].problem("secret", 1) == "is no argument of the tool"
