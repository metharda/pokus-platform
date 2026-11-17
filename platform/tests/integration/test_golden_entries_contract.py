"""The model of a golden image's entry accepts what the pinned contract's schema accepts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from potka_core.images import GoldenEntry
from pydantic import ValidationError

SCHEMA = Path("contract") / "records" / "fingerprint.schema.json"
ENTRY: dict[str, Any] = {
    "fingerprint": "ab" * 32,
    "type": "container",
    "nesting": False,
    "packages": [{"name": "curl", "version": "8.5.0-2", "architecture": "amd64"}],
}
SAMPLES: list[Any] = [
    ENTRY,
    {**ENTRY, "type": "vm", "nesting": True},
    {**ENTRY, "packages": []},
    {**ENTRY, "packages": None},
    {
        **ENTRY,
        "packages": [*ENTRY["packages"], {"name": "a", "version": "1", "architecture": "all"}],
    },
    {**ENTRY, "fingerprint": None},
    {**ENTRY, "fingerprint": 7},
    {**ENTRY, "type": "pod"},
    {**ENTRY, "type": None},
    {**ENTRY, "nesting": "false"},
    {**ENTRY, "nesting": 0},
    {**ENTRY, "nesting": None},
    {**ENTRY, "packages": "curl"},
    {**ENTRY, "packages": ["curl"]},
    {**ENTRY, "packages": [{"name": "curl", "version": "8.5.0-2"}]},
    {**ENTRY, "packages": [{"name": "", "version": "8.5.0-2", "architecture": "amd64"}]},
    {**ENTRY, "packages": [{"name": "curl", "version": "", "architecture": "amd64"}]},
    {**ENTRY, "packages": [{"name": "curl", "version": "8.5.0-2", "architecture": ""}]},
    {**ENTRY, "packages": [{**ENTRY["packages"][0], "status": "installed"}]},
    {**ENTRY, "requirements": {"nesting": True}},
    {key: value for key, value in ENTRY.items() if key != "fingerprint"},
    {key: value for key, value in ENTRY.items() if key != "type"},
    {key: value for key, value in ENTRY.items() if key != "nesting"},
    {key: value for key, value in ENTRY.items() if key != "packages"},
    {"fingerprint": "ab" * 32, "packages": ENTRY["packages"]},
    "ab" * 32,
    None,
    [],
    {},
]


def accepted(sample: Any) -> bool:
    try:
        GoldenEntry.model_validate_json(json.dumps(sample), strict=True)
    except ValidationError:
        return False
    return True


@pytest.fixture(scope="module")
def contract_entry(infra_checkout: Path) -> jsonschema.Draft202012Validator:
    schema = json.loads((infra_checkout / SCHEMA).read_text(encoding="utf-8"))
    software = schema["$defs"]["fingerprint"]["properties"]["software"]["properties"]
    assert software["golden_images"]["additionalProperties"] == {"$ref": "#/$defs/golden_image"}
    entry = {"$ref": "#/$defs/golden_image", "$defs": schema["$defs"]}
    jsonschema.Draft202012Validator.check_schema(entry)
    return jsonschema.Draft202012Validator(entry)


@pytest.mark.parametrize("sample", SAMPLES, ids=range(len(SAMPLES)))
def test_the_model_and_the_contract_s_schema_accept_the_same_entries(
    contract_entry: jsonschema.Draft202012Validator, sample: Any
) -> None:
    assert accepted(sample) == contract_entry.is_valid(sample)


def test_the_samples_are_of_both_kinds(contract_entry: jsonschema.Draft202012Validator) -> None:
    valid = [sample for sample in SAMPLES if contract_entry.is_valid(sample)]
    assert valid == SAMPLES[:5]


@pytest.mark.parametrize(
    "fingerprint",
    ["", "ab", "ab" * 31 + "a", "ab" * 32 + "a", "AB" * 32, "ag" * 32, "ab" * 32 + "\n"],
)
def test_only_a_text_that_is_no_whole_fingerprint_is_refused_beyond_the_contract(
    contract_entry: jsonschema.Draft202012Validator, fingerprint: str
) -> None:
    sample = {**ENTRY, "fingerprint": fingerprint}
    assert contract_entry.is_valid(sample)
    assert not accepted(sample)
