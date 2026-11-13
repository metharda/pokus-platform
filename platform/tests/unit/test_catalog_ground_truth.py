"""ground_truth.yaml: the fault, the accepted diagnosis, the actions and the probes."""

from __future__ import annotations

import json
from typing import Any

import pytest
from potka_core.catalog.ground_truth import GroundTruth
from potka_testing.catalog import control_data, ground_truth_data
from pydantic import ValidationError


def load(data: dict[str, Any]) -> GroundTruth:
    return GroundTruth.model_validate_json(json.dumps(data))


def test_a_ground_truth_loads() -> None:
    truth = load(ground_truth_data())
    assert truth.fault.category == "fixture.config.syntax_error"
    assert truth.fault.component == "the web server"
    assert truth.diagnosis.accepted_labels == ("fixture.config.syntax_error", "fixture")
    assert truth.labels() == (
        "fixture.config.syntax_error",
        "fixture.config.syntax_error",
        "fixture",
    )
    assert [action.tool for action in truth.actions.all()] == [
        "apply_config_patch",
        "nginx_reload",
        "stop_service",
    ]
    assert truth.verification[0].probe == "http_get"
    assert truth.collateral[0].params == {"node": "web", "paths": ["/srv/"]}
    assert truth.placeholders() == ("site", "site", "port")
    assert truth.actions.acceptable[0].placeholders() == ("site",)


def test_a_ground_truth_without_a_fault_loads() -> None:
    truth = load(control_data())
    assert (truth.fault.category, truth.fault.component, truth.fault.location) == (
        "none",
        None,
        None,
    )
    assert truth.actions.all() == ()
    assert truth.placeholders() == ()
    assert truth.labels() == ("none", "none")


@pytest.mark.parametrize(
    ("data", "change", "message"),
    [
        (
            ground_truth_data,
            lambda d: d["fault"].update(category="fixture.disk_full"),
            "the fault's category is not among the accepted labels",
        ),
        (
            ground_truth_data,
            lambda d: d["diagnosis"]["accepted_labels"].append("fixture"),
            "accepted_labels lists a label twice",
        ),
        (
            ground_truth_data,
            lambda d: d["fault"].update(component=None),
            "only the category none has neither",
        ),
        (
            ground_truth_data,
            lambda d: d["fault"].update(location=None),
            "only the category none has neither",
        ),
        (
            ground_truth_data,
            lambda d: d["fault"].update(component=None, location=None),
            "only the category none has neither",
        ),
        (
            control_data,
            lambda d: d["fault"].update(component="the web server"),
            "only the category none has neither",
        ),
        (
            control_data,
            lambda d: d["fault"].update(location="/etc"),
            "only the category none has neither",
        ),
        (
            ground_truth_data,
            lambda d: d["fault"].update(location="/srv/$site"),
            r"the '\$' at character 6 is neither",
        ),
        (
            ground_truth_data,
            lambda d: d["actions"]["harmful"][0]["args"].update(unit="${Unit}"),
            r"unit: the '\$' at character 1 is neither",
        ),
        (
            ground_truth_data,
            lambda d: d["collateral"][0]["params"]["paths"].append("/var/$"),
            r"paths\.1: the '\$' at character 6 is neither",
        ),
        (
            ground_truth_data,
            lambda d: d["verification"][0]["params"].update(timeout=float("inf")),
            "NaN or an infinity is not a catalog value",
        ),
    ],
)
def test_a_ground_truth_that_contradicts_itself_is_refused(
    data: Any, change: Any, message: str
) -> None:
    made = data()
    change(made)
    with pytest.raises(ValidationError, match=message):
        GroundTruth.model_validate_json(json.dumps(made))


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["fault"].update(category="Fixture"),
        lambda d: d["fault"].update(category=""),
        lambda d: d["fault"].update(component=""),
        lambda d: d["fault"].update(location=""),
        lambda d: d["fault"].update(severity="high"),
        lambda d: d["fault"].pop("component"),
        lambda d: d["fault"].pop("location"),
        lambda d: d["diagnosis"].update(accepted_labels=[]),
        lambda d: d["diagnosis"].update(accepted_labels="fixture.config"),
        lambda d: d["diagnosis"].update(rejected_labels=[]),
        lambda d: d["actions"].pop("then_required"),
        lambda d: d["actions"].update(neutral=[]),
        lambda d: d["actions"]["acceptable"][0].update(tool="apply-config-patch"),
        lambda d: d["actions"]["acceptable"][0].update(tool="file.write"),
        lambda d: d["actions"]["acceptable"][0].pop("args"),
        lambda d: d["actions"]["acceptable"][0].update(args=[]),
        lambda d: d["actions"]["acceptable"][0].update(confidence=0.5),
        lambda d: d["verification"][0].update(probe="http.get"),
        lambda d: d["verification"][0].update(primitive="file.write"),
        lambda d: d["verification"][0].pop("params"),
        lambda d: d.update(resolution_state=""),
        lambda d: d.update(resolution_state=" \n\t"),
        lambda d: d["fault"].update(component="  "),
        lambda d: d["fault"].update(location=" "),
        lambda d: d.update(resolution_state=None),
        lambda d: d.update(ticket="x"),
        lambda d: d.update(id="FIXTURE-001"),
    ],
)
def test_a_malformed_ground_truth_is_refused(change: Any) -> None:
    data = ground_truth_data()
    change(data)
    with pytest.raises(ValidationError) as caught:
        load(data)
    assert caught.value.error_count() == 1


def test_a_validation_error_shows_no_value_of_the_file() -> None:
    data = ground_truth_data()
    data["fault"].update(category="S3CRET.label", location="s3cret $")
    data["diagnosis"]["accepted_labels"] = ["s3cret.one", "s3cret.one"]
    data["actions"]["harmful"][0].update(tool="s3cret-tool")
    with pytest.raises(ValidationError) as caught:
        load(data)
    assert caught.value.error_count() == 4
    assert "s3cret" not in str(caught.value).lower()
    assert "s3cret" not in repr(caught.value).lower()


@pytest.mark.parametrize("key", sorted(ground_truth_data()))
def test_every_key_of_a_ground_truth_is_required(key: str) -> None:
    data = ground_truth_data()
    del data[key]
    with pytest.raises(ValidationError) as caught:
        load(data)
    assert [(error["type"], error["loc"]) for error in caught.value.errors()] == [
        ("missing", (key,))
    ]


@pytest.mark.parametrize(
    ("change", "errors"),
    [
        # The one label is refused, and then none is left.
        (lambda d: d["diagnosis"].update(accepted_labels=["fixture config"]), 2),
        # A step is a probe or a primitive: the wrong one is a missing key and an unknown one.
        (lambda d: d["collateral"].append({"primitive": "file.write", "params": {}}), 2),
    ],
)
def test_a_value_no_alternative_accepts_is_refused_once_for_each(change: Any, errors: int) -> None:
    data = ground_truth_data()
    change(data)
    with pytest.raises(ValidationError) as caught:
        load(data)
    assert caught.value.error_count() == errors
