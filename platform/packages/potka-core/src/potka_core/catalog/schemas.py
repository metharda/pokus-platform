"""The JSON Schemas of the catalog's files, exported from their models.

The schemas describe each file's form to the people who write a catalog and
to their editors: every field with its description, type and pattern. A
model checks more than its schema can say (what the fields of one file say
about each other), so a file that matches its schema may still be refused.

A reader may also evaluate a schema's pattern differently from the models.
In the models, ``$`` matches only at the very end of a value. In a reader
that evaluates patterns with Python's ``re`` module, as the ``jsonschema``
package does, ``$`` also matches before a final line break, so such a
reader accepts some values ending in a line break that the models refuse.
Every schema says so in its ``$comment``.
"""

from __future__ import annotations

from typing import Any

from potka_core.catalog.base import Model
from potka_core.catalog.blueprint import Blueprint
from potka_core.catalog.ground_truth import GroundTruth
from potka_core.catalog.scenario import Scenario
from potka_core.catalog.taxonomy import Taxonomy

DIALECT = "https://json-schema.org/draft/2020-12/schema"
NOTE = (
    "In the models that read these files, $ in a pattern matches only at the very end of a "
    "value. In a reader that evaluates patterns with Python's re module, as the jsonschema "
    "package does, $ also matches before a final line break, so such a reader accepts some "
    "values ending in a line break that the models refuse."
)
"""What every schema's ``$comment`` says of patterns."""
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
        f"{name}.schema.json": {
            "$schema": DIALECT,
            "$comment": NOTE,
            **model.model_json_schema(),
        }
        for name, model in MODELS.items()
    }
