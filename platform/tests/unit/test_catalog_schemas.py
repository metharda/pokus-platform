"""The exported JSON Schemas describe every field and accept what the models accept."""

from __future__ import annotations

from typing import Any

import jsonschema
import pytest
from potka_core.catalog.schemas import schemas
from potka_testing.catalog import (
    control_data,
    ground_truth_data,
    scenario_data,
    taxonomy_data,
)
from potka_testing.catalog_tree import blueprint_data

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
