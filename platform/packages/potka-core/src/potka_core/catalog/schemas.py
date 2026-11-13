"""The JSON Schemas of the catalog's files, exported from their models.

The schemas describe each file's form to the people who write a catalog and
to their editors: every field with its description, type and pattern. A
model checks more than its schema can say (what the fields of one file say
about each other), so a file that matches its schema may still be refused.
"""

from __future__ import annotations

from typing import Any

from potka_core.catalog.base import Model
from potka_core.catalog.blueprint import Blueprint
from potka_core.catalog.ground_truth import GroundTruth
from potka_core.catalog.scenario import Scenario
from potka_core.catalog.taxonomy import Taxonomy

DIALECT = "https://json-schema.org/draft/2020-12/schema"
MODELS: dict[str, type[Model]] = {
    "blueprint": Blueprint,
    "ground_truth": GroundTruth,
    "scenario": Scenario,
    "taxonomy": Taxonomy,
}


def schemas() -> dict[str, dict[str, Any]]:
    """The JSON Schema of each of the catalog's files.

    Returns:
        Each schema by its file name, ``<format>.schema.json``.
    """
    return {
        f"{name}.schema.json": {"$schema": DIALECT, **model.model_json_schema()}
        for name, model in MODELS.items()
    }
