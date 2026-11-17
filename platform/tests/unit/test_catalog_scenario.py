"""scenario.yaml: a scenario template, its steps, its parameters and its ticket."""

from __future__ import annotations

import json
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from potka_core.catalog.parameters import SEED_MAX, SEED_MIN, TemplateError
from potka_core.catalog.scenario import MAX_SETTLE_SECONDS, Scenario
from potka_core.catalog.steps import Probe, Step
from potka_testing.catalog import scenario_data
from pydantic import ValidationError


def load(data: dict[str, Any]) -> Scenario:
    return Scenario.model_validate_json(json.dumps(data))


def test_a_scenario_loads() -> None:
    scenario = load(scenario_data())
    assert (scenario.id, scenario.version, scenario.split) == ("FIXTURE-001", 2, "dev")
    assert (scenario.risk_level, scenario.isolation) == ("low", "container")
    assert scenario.blueprint == "fixture-web"
    assert scenario.tags == ("web", "config-error")
    assert (scenario.confirm_timeout_seconds, scenario.settle_seconds) == (30, 5)
    assert scenario.budgets_override is None
    kinds: list[type[Probe | Step]] = [Probe, Step, Step, Probe]
    assert [type(step) for step in scenario.steps()] == kinds
    assert scenario.inject[0].primitive == "nginx.corrupt_conf"
    assert scenario.confirm[0].probe == "nginx_config_valid"
    assert scenario.placeholders() == ("site", "port", "port", "site", "site")


def test_an_instance_has_the_values_its_seed_derives() -> None:
    scenario = load(scenario_data())
    values = scenario.values(1)
    # HMAC-SHA256 of "port" with the seed 1 is 0 modulo 5 (the parameter tests' digest).
    assert values["port"] == 8081
    assert values["site"] in ("blog", "shop", "docs")
    assert list(values) == ["site", "port"]
    assert scenario.render_ticket(values) == (
        f"The {values['site']} site on port 8081 stopped answering. It costs us $ by the hour."
    )
    assert scenario.preconditions[0].render(values) == {"node": "web", "port": 8081, "status": 200}
    assert scenario.setup[0].render(values)["path"] == f"/srv/{values['site']}/index.html"
    assert scenario.inject[0].placeholders() == ("site",)
    assert scenario.confirm[0].render(values) == {"node": "web", "expect": False}
    assert scenario.preconditions[0].params["port"] == "${port}"


@given(seed=st.integers(min_value=SEED_MIN, max_value=SEED_MAX))
def test_every_seed_resolves_a_valid_scenario(seed: int) -> None:
    scenario = load(scenario_data())
    values = scenario.values(seed)
    assert values["site"] in ("blog", "shop", "docs")
    assert values["port"] in range(8081, 8086)
    ticket = scenario.render_ticket(values)
    assert "${" not in ticket
    assert str(values["port"]) in ticket
    for step in scenario.steps():
        assert "${" not in json.dumps(step.render(values))
    assert scenario.values(seed) == values


def test_a_step_without_its_values_is_refused() -> None:
    scenario = load(scenario_data())
    with pytest.raises(TemplateError, match=r"^no parameter site$"):
        scenario.inject[0].render({"port": 8081})
    with pytest.raises(TemplateError, match=r"^no parameter site$"):
        scenario.render_ticket({"port": 8081})


def test_budgets_may_be_replaced() -> None:
    data = scenario_data()
    data["budgets_override"] = {"max_steps": 12, "max_wall_s": 600}
    budgets = load(data).budgets_override
    assert budgets is not None
    assert budgets.model_dump() == {
        "max_steps": 12,
        "max_tool_calls": None,
        "max_tokens_total": None,
        "max_wall_s": 600,
    }


def test_a_scenario_without_parameters_or_steps_loads() -> None:
    data = scenario_data()
    data.update(parameters={}, preconditions=[], setup=[], inject=[], confirm=[], tags=[])
    data.update(ticket="Nothing seems wrong.", settle_seconds=0, confirm_timeout_seconds=1)
    scenario = load(data)
    assert scenario.values(7) == {}
    assert scenario.steps() == ()
    assert scenario.render_ticket({}) == "Nothing seems wrong."


@pytest.mark.parametrize(
    "scenario_id",
    ["FIXTURE-001", "FIXTURE-CONTROL-000", "NGINX-001", "A-999", "DB2-010", "FIXTURE-X9-123"],
)
def test_the_forms_of_a_scenario_id(scenario_id: str) -> None:
    data = scenario_data()
    data["id"] = scenario_id
    assert load(data).id == scenario_id


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda d: d.update(ticket="The ${place} is down."),
            "a template uses an undeclared parameter: place",
        ),
        (
            lambda d: d["inject"][0]["params"].update(extra=["${b}", {"k": "${a}"}]),
            "a template uses an undeclared parameter: a, b",
        ),
        (
            lambda d: d["preconditions"][0]["params"].update(port="${Port}"),
            r"port: the '\$' at character 1 is neither '\$\$' nor the start of '\$\{name\}'",
        ),
        (
            lambda d: d["inject"][0]["params"].update(deep={"list": ["ok", {"key": "a $ b"}]}),
            r"deep\.list\.1\.key: the '\$' at character 3 is neither",
        ),
        (
            lambda d: d.update(ticket="It costs 5 $ a day."),
            r"the '\$' at character 12 is neither",
        ),
        (lambda d: d["tags"].append("web"), "tag given twice: web"),
        (lambda d: d.update(budgets_override={}), "budgets_override replaces no budget"),
        (
            lambda d: d.update(budgets_override={"max_steps": None}),
            "budgets_override replaces no budget",
        ),
        (
            lambda d: d["setup"][0]["params"].update(size=2**53),
            "an integer beyond 2\\*\\*53 - 1 is not a catalog value",
        ),
    ],
)
def test_a_scenario_that_contradicts_itself_is_refused(change: Any, message: str) -> None:
    data = scenario_data()
    change(data)
    with pytest.raises(ValidationError, match=message):
        load(data)


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(id="fixture-001"),
        lambda d: d.update(id="FIXTURE-01"),
        lambda d: d.update(id="FIXTURE-0001"),
        lambda d: d.update(id="FIXTURE_001"),
        lambda d: d.update(id="001-FIXTURE"),
        lambda d: d.update(id="FIXTURE-001\n"),
        lambda d: d.update(id="FIXTURE--001"),
        lambda d: d.update(version=0),
        lambda d: d.update(version="2"),
        lambda d: d.update(version=2.0),
        lambda d: d.update(title=""),
        lambda d: d.update(title="  "),
        lambda d: d.update(ticket=" \n"),
        lambda d: d.update(version=2**53),
        lambda d: d.update(confirm_timeout_seconds=2**53),
        lambda d: d.update(budgets_override={"max_tokens_total": 2**53}),
        lambda d: d.update(risk_level="none"),
        lambda d: d.update(isolation="chroot"),
        lambda d: d.update(blueprint="Fixture_Web"),
        lambda d: d.update(blueprint="fixture-web-"),
        lambda d: d.update(split="validation"),
        lambda d: d.update(split=None),
        lambda d: d.update(tags="web"),
        lambda d: d.update(tags=["Web"]),
        lambda d: d.update(parameters=[]),
        lambda d: d["parameters"].update(Site={"type": "choice", "values": ["a"]}),
        lambda d: d["parameters"].update({"a-b": {"type": "choice", "values": ["a"]}}),
        lambda d: d["parameters"]["site"].update(values=[]),
        lambda d: d["inject"][0].update(primitive="corrupt_conf"),
        lambda d: d["inject"][0].update(primitive="nginx.corrupt.conf"),
        lambda d: d["inject"][0].update(primitive="Nginx.corrupt_conf"),
        lambda d: d["inject"][0].update(probe="http_get"),
        lambda d: d["inject"][0].pop("params"),
        lambda d: d["inject"][0].update(params=["node"]),
        lambda d: d["inject"][0].update(node="web"),
        lambda d: d["confirm"][0].update(probe="nginx.config_valid"),
        lambda d: d["confirm"][0].update(probe="Http_get"),
        lambda d: d["confirm"][0].update(primitive="file.write"),
        lambda d: d["confirm"].append("http_get"),
        lambda d: d.update(confirm_timeout_seconds=0),
        lambda d: d.update(confirm_timeout_seconds=-30),
        lambda d: d.update(confirm_timeout_seconds=1.5),
        lambda d: d.update(confirm_timeout_seconds="30"),
        lambda d: d.update(confirm_timeout_seconds=True),
        lambda d: d.update(confirm_timeout_seconds=None),
        lambda d: d.update(settle_seconds=-1),
        lambda d: d.update(settle_seconds=MAX_SETTLE_SECONDS + 1),
        lambda d: d.update(settle_seconds=1.5),
        lambda d: d.update(settle_seconds="5"),
        lambda d: d.update(ticket=""),
        lambda d: d.update(ticket=["a"]),
        lambda d: d.update(budgets_override={"max_steps": 0}),
        lambda d: d.update(budgets_override={"max_steps": 5, "max_mutating_calls": 1}),
        lambda d: d.update(ground_truth={}),
        lambda d: d.update(confirm_timeout=30),
    ],
)
def test_a_malformed_scenario_is_refused(change: Any) -> None:
    data = scenario_data()
    change(data)
    with pytest.raises(ValidationError) as caught:
        load(data)
    assert caught.value.error_count() == 1


def test_a_validation_error_shows_no_value_of_the_file() -> None:
    data = scenario_data()
    data.update(split="s3cret-split", settle_seconds="s3cret-seconds")
    data["inject"][0]["params"].update(marker="s3cret-param $")
    data["parameters"]["site"]["values"] = ["s3cret-value", "s3cret-value"]
    data["parameters"]["port"]["type"] = "s3cret-type"
    with pytest.raises(ValidationError) as caught:
        load(data)
    assert caught.value.error_count() == 5
    messages = [error["msg"] for error in caught.value.errors()]
    assert "a parameter's type is choice or range" in messages
    assert "s3cret" not in str(messages)
    assert "s3cret" not in str(caught.value)
    assert "s3cret" not in repr(caught.value)
    assert "s3cret" not in str(caught.value.errors(include_input=False, include_url=False))
    # errors() and json() carry the input unless asked not to: whoever prints them asks.
    assert "s3cret" in str(caught.value.errors())


def test_an_errors_place_names_keys_of_the_file_and_no_value() -> None:
    data = scenario_data()
    data["unknown_key"] = "s3cret-value"
    data["inject"][0]["params"]["odd key"] = "s3cret $"
    with pytest.raises(ValidationError) as caught:
        load(data)
    places = [".".join(str(part) for part in error["loc"]) for error in caught.value.errors()]
    assert places == ["inject.0.params", "unknown_key"]
    assert "odd key: the '$' at character 8" in str(caught.value)
    assert "s3cret" not in str(caught.value)


def test_the_largest_counts_are_accepted() -> None:
    data = scenario_data()
    data.update(version=2**53 - 1, confirm_timeout_seconds=2**53 - 1)
    data["budgets_override"] = {"max_tokens_total": 2**53 - 1}
    assert load(data).version == 2**53 - 1


def test_a_scenarios_fields_cannot_be_reassigned() -> None:
    scenario = load(scenario_data())
    with pytest.raises(ValidationError):
        scenario.split = "train"
    with pytest.raises(ValidationError):
        scenario.inject[0].primitive = "file.write"


@pytest.mark.parametrize("key", sorted(scenario_data()))
def test_every_key_of_a_scenario_but_budgets_override_is_required(key: str) -> None:
    data = scenario_data()
    del data[key]
    with pytest.raises(ValidationError) as caught:
        load(data)
    assert [(error["type"], error["loc"]) for error in caught.value.errors()] == [
        ("missing", (key,))
    ]
