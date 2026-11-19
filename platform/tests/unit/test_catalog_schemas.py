"""The exported JSON Schemas describe every field and accept what the models accept."""

from __future__ import annotations

import json
from typing import Any

import jsonschema
import pytest
from potka_core.catalog.scenario import Scenario
from potka_core.catalog.schemas import MODELS, NOTE, schemas
from potka_testing.catalog import (
    control_data,
    ground_truth_data,
    scenario_data,
    taxonomy_data,
)
from potka_testing.catalog_tree import blueprint_data
from pydantic import ValidationError

SAMPLES = [
    ("blueprint.schema.json", blueprint_data),
    ("ground_truth.schema.json", ground_truth_data),
    ("ground_truth.schema.json", control_data),
    ("scenario.schema.json", scenario_data),
    ("taxonomy.schema.json", taxonomy_data),
]


def validator(name: str) -> jsonschema.Draft202012Validator:
    schema = schemas()[name]
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def objects(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return [schema, *schema.get("$defs", {}).values()]


def test_there_is_one_schema_for_each_file_of_the_catalog() -> None:
    assert sorted(schemas()) == sorted({name for name, _ in SAMPLES})
    for schema in schemas().values():
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_every_schema_carries_the_note_on_patterns() -> None:
    for name, schema in schemas().items():
        assert schema["$comment"] == NOTE, name


def _ends_in_a_line_break(data: Any, place: list[str | int]) -> None:
    for key in place[:-1]:
        data = data[key]
    data[place[-1]] += "\n"


PATTERN = "string_pattern_mismatch"


@pytest.mark.parametrize(
    ("name", "sample", "place", "refusal"),
    [
        ("blueprint.schema.json", blueprint_data, ["name"], PATTERN),
        ("blueprint.schema.json", blueprint_data, ["nodes", 0, "alias"], PATTERN),
        ("blueprint.schema.json", blueprint_data, ["nodes", 0, "services", 0], PATTERN),
        ("blueprint.schema.json", blueprint_data, ["nodes", 0, "endpoints", 0, "path"], PATTERN),
        # This pattern takes the line break itself; the model refuses it by another rule.
        (
            "blueprint.schema.json",
            blueprint_data,
            ["nodes", 0, "allowed_read_paths", 0],
            "value_error",
        ),
        ("ground_truth.schema.json", ground_truth_data, ["fault", "category"], PATTERN),
        ("scenario.schema.json", scenario_data, ["id"], PATTERN),
        ("scenario.schema.json", scenario_data, ["inject", 0, "primitive"], PATTERN),
        ("scenario.schema.json", scenario_data, ["preconditions", 0, "probe"], PATTERN),
        ("taxonomy.schema.json", taxonomy_data, ["labels", 0, "name"], PATTERN),
    ],
)
def test_the_note_is_true_of_a_value_that_ends_in_a_line_break(
    name: str, sample: Any, place: list[str | int], refusal: str
) -> None:
    model = MODELS[name.removesuffix(".schema.json")]
    data = sample()
    model.model_validate_json(json.dumps(data))
    _ends_in_a_line_break(data, place)
    # This reader accepts the value, and the model refuses it at that place.
    assert list(validator(name).iter_errors(data)) == []
    with pytest.raises(ValidationError) as refused:
        model.model_validate_json(json.dumps(data))
    assert [error["loc"] for error in refused.value.errors()] == [tuple(place)]
    assert refused.value.errors()[0]["type"] == refusal


def test_a_text_may_end_in_a_line_break_for_the_models_too() -> None:
    # The note is about patterns that end in $: a text has none.
    data = scenario_data()
    data["title"] += "\n"
    assert list(validator("scenario.schema.json").iter_errors(data)) == []
    assert Scenario.model_validate_json(json.dumps(data)).title == data["title"]


def test_every_model_and_every_field_is_described() -> None:
    for name, schema in schemas().items():
        for model in objects(schema):
            if "properties" not in model:
                continue
            assert model["description"].strip(), (name, model["title"])
            assert model["additionalProperties"] is False, (name, model["title"])
            for field, described in model["properties"].items():
                assert described.get("description", "").strip(), (name, model["title"], field)


@pytest.mark.parametrize(("name", "sample"), SAMPLES)
def test_a_schema_accepts_a_valid_file(name: str, sample: Any) -> None:
    assert list(validator(name).iter_errors(sample())) == []


@pytest.mark.parametrize(
    ("name", "sample", "change", "place"),
    [
        ("scenario.schema.json", scenario_data, lambda d: d.update(id="fixture-1"), ["id"]),
        ("scenario.schema.json", scenario_data, lambda d: d.update(split="all"), ["split"]),
        ("scenario.schema.json", scenario_data, lambda d: d.update(extra=1), []),
        ("scenario.schema.json", scenario_data, lambda d: d.pop("ticket"), []),
        (
            "scenario.schema.json",
            scenario_data,
            lambda d: d["parameters"]["port"].update(step=0),
            ["parameters", "port"],
        ),
        (
            "ground_truth.schema.json",
            ground_truth_data,
            lambda d: d["diagnosis"].update(accepted_labels=[]),
            ["diagnosis", "accepted_labels"],
        ),
        (
            "taxonomy.schema.json",
            taxonomy_data,
            lambda d: d["labels"][0].update(name="None"),
            ["labels", 0, "name"],
        ),
        (
            "blueprint.schema.json",
            blueprint_data,
            lambda d: d["nodes"][0]["endpoints"][0].update(port=0),
            ["nodes", 0, "endpoints", 0, "port"],
        ),
    ],
)
def test_a_schema_refuses_a_malformed_file(
    name: str, sample: Any, change: Any, place: list[str | int]
) -> None:
    data = sample()
    change(data)
    errors = list(validator(name).iter_errors(data))
    assert errors
    assert [list(error.absolute_path) for error in errors] == [place]
