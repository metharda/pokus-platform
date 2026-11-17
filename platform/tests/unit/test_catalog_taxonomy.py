"""taxonomy.yaml: the labels of a diagnosis."""

from __future__ import annotations

import json
from typing import Any

import pytest
from potka_core.catalog.taxonomy import NONE, UNKNOWN, Taxonomy
from potka_testing.catalog import taxonomy_data
from pydantic import ValidationError


def load(data: dict[str, Any]) -> Taxonomy:
    return Taxonomy.model_validate_json(json.dumps(data))


def test_a_taxonomy_loads() -> None:
    taxonomy = load(taxonomy_data())
    assert taxonomy.version == 1
    assert taxonomy.names() == (
        "none",
        "unknown",
        "fixture",
        "fixture.config.syntax_error",
        "fixture.disk_full",
    )
    assert (NONE, UNKNOWN) == ("none", "unknown")
    assert taxonomy.labels[3].description == "A file cannot be parsed."


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d["labels"].pop(0), "the label none is missing"),
        (lambda d: d["labels"].pop(1), "the label unknown is missing"),
        (lambda d: d["labels"].append(dict(d["labels"][2])), "label given twice: fixture"),
        (
            lambda d: d["labels"].append({"name": "none.really", "description": "x"}),
            "label below none or unknown: none.really",
        ),
        (
            lambda d: d["labels"].append({"name": "unknown.kind.of", "description": "x"}),
            "label below none or unknown: unknown.kind.of",
        ),
    ],
)
def test_a_taxonomy_that_contradicts_itself_is_refused(change: Any, message: str) -> None:
    data = taxonomy_data()
    change(data)
    with pytest.raises(ValidationError, match=message):
        load(data)


@pytest.mark.parametrize(
    "name", ["nonesuch", "none_of_these", "unknowns", "fixture.none", "fixture.unknown.x"]
)
def test_a_label_that_only_resembles_none_or_unknown_is_accepted(name: str) -> None:
    data = taxonomy_data()
    data["labels"].append({"name": name, "description": "x"})
    assert name in load(data).names()


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(version=0),
        lambda d: d.update(version=2**53),
        lambda d: d["labels"][2].update(description=" "),
        lambda d: d.update(version="1"),
        lambda d: d.update(version=1.0),
        lambda d: d.update(version=True),
        lambda d: d.pop("version"),
        lambda d: d.update(extra=1),
        lambda d: d.update(labels={"none": "x"}),
        lambda d: d["labels"][2].update(name="Fixture"),
        lambda d: d["labels"][2].update(name="fixture."),
        lambda d: d["labels"][2].update(name=".fixture"),
        lambda d: d["labels"][2].update(name="fixture..x"),
        lambda d: d["labels"][2].update(name="fixture.9x"),
        lambda d: d["labels"][2].update(name="fixture-x"),
        lambda d: d["labels"][2].update(name="fixture x"),
        lambda d: d["labels"][2].update(name="fixture\n"),
        lambda d: d["labels"][2].update(name=""),
        lambda d: d["labels"][2].update(description=""),
        lambda d: d["labels"][2].pop("description"),
        lambda d: d["labels"][2].update(parent="none"),
        lambda d: d["labels"].append("fixture.other"),
    ],
)
def test_a_malformed_taxonomy_is_refused(change: Any) -> None:
    data = taxonomy_data()
    change(data)
    with pytest.raises(ValidationError) as caught:
        load(data)
    assert caught.value.error_count() == 1
