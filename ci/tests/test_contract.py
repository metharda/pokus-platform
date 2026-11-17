from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

import pytest
from conftest import Repo
from potka_core.endpoints import Endpoints
from potka_core.images import GoldenEntry
from potka_core.principals import Principals
from pydantic import BaseModel

from potka_ci import contract

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["name"],
    "properties": {"name": {"type": "string"}},
}
LAB_POSTGRES = (
    "docker.io/library/postgres:18.6-trixie"
    "@sha256:5a5a84b19854a9ffaa54082c166ff4ec27473a361e496e5ea167f298f2da9722"
)
VERSIONS = """| Name | Image | Tag | Digest | Platforms |
| --- | --- | --- | --- | --- |
| otel_collector | `docker.io/otel/collector` | `0.1` | `sha256:{other}` | linux/amd64 |
| postgres | `docker.io/library/postgres` | `18.6-trixie` | `sha256:{digest}` | linux/amd64 |
""".format(other="1" * 64, digest=LAB_POSTGRES.rsplit(":", 1)[1])
WORKFLOW = "jobs:\n  test:\n    services:\n      postgres:\n        image: {image}\n"
FAKE_VALIDATOR = """
import pathlib, sys
if sys.argv[1] != "validate" or sys.argv[3:] != ["--no-host-check"]:
    sys.stderr.write("usage: " + " ".join(sys.argv) + "\\n")
    sys.exit(2)
release = pathlib.Path(sys.argv[2])
if (release / "refuse").exists():
    sys.stderr.write("services[0]: refused\\n")
    sys.exit(65)
print("platform potka 0.0.0 (python-uv)")
"""


OURS: dict[str, type[BaseModel]] = {
    "Principals": Principals,
    "Endpoints": Endpoints,
    "GoldenEntry": GoldenEntry,
}


def stand_in(model: contract.Model) -> dict[str, Any]:
    """A schema file for MODEL: the model's own schema, as the definition it is of if any."""
    schema: dict[str, Any] = OURS[model.target.partition(":")[2]].model_json_schema(by_alias=True)
    if model.part is None:
        return schema
    defs = schema.pop("$defs", {})
    assert model.part not in defs
    return {"type": "object", "$defs": {**defs, model.part: schema}}


@pytest.fixture
def infra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repo:
    """A stand-in pokus-infrastructure checkout with origin/main.

    Its model schemas are the models' own, and the check expects their sha256.
    """
    r = Repo(tmp_path / "infra")
    r.write("contract/platform-manifest.schema.json", json.dumps(SCHEMA))
    models = []
    for model in contract.MODELS:
        text = json.dumps(stand_in(model))
        r.write(model.schema.as_posix(), text)
        models.append(model._replace(digest=hashlib.sha256(text.encode()).hexdigest()))
    monkeypatch.setattr(contract, "MODELS", tuple(models))
    r.write("tools/platform/pinka_platform/__init__.py", "")
    r.write("tools/platform/pinka_platform/__main__.py", FAKE_VALIDATOR)
    pin = f"pyyaml=={metadata.version('pyyaml')}"
    r.write("pyproject.toml", f'[dependency-groups]\ndev = ["{pin}"]\n')
    r.write("docs/reference/versions.md", VERSIONS)
    r.git("add", "-A")
    r.commit("The contract")
    bare = tmp_path / "infra.git"
    r.git("init", "-q", "--bare", str(bare))
    r.git("remote", "add", "origin", str(bare))
    r.git("push", "-q", "origin", "main")
    r.git("fetch", "-q", "origin")
    return r


def platform_repo(
    repo: Repo, pin: str, manifest: str = "name: potka\n", image: str = LAB_POSTGRES
) -> Repo:
    repo.write("INFRA_PIN", pin + "\n")
    repo.write("platform/platform-manifest.yaml", manifest)
    repo.write(".github/workflows/ci.yml", WORKFLOW.format(image=image))
    return repo


def test_without_a_checkout_it_is_skipped(repo: Repo) -> None:
    outcome = contract.run(repo.path, None)
    assert outcome.status == "skipped"


def test_the_release_meets_the_contract(repo: Repo, infra: Repo) -> None:
    pin = infra.head()
    outcome = contract.run(platform_repo(repo, pin).path, infra.path)
    assert outcome.status == "passed", outcome.lines
    assert outcome.lines[0] == f"INFRA_PIN {pin} is on pokus-infrastructure's main"
    assert "the validator accepts the release:" in outcome.lines
    assert "  platform potka 0.0.0 (python-uv)" in outcome.lines
    assert [(model.name, model.place) for model in contract.MODELS] == [
        ("principals.json", "contract/principals.schema.json"),
        ("endpoints.toml", "contract/endpoints.schema.json"),
        (
            "a golden image's entry in the fingerprint",
            "golden_image in contract/records/fingerprint.schema.json",
        ),
    ]
    for model in contract.MODELS:
        assert (
            f"potka-core's model of {model.name} has the fields of {model.place},"
            f" the schema it was written for (sha256 {model.digest[:12]})"
        ) in outcome.lines
    assert (
        f"the test job's PostgreSQL is the lab's (docs/reference/versions.md): {LAB_POSTGRES}"
    ) in outcome.lines
    assert outcome.lines[-1] == f"PyYAML {metadata.version('pyyaml')}, as pokus-infrastructure pins"


def test_the_workflow_s_postgres_must_be_the_lab_s(repo: Repo, infra: Repo) -> None:
    other = LAB_POSTGRES[:-1] + "0"
    outcome = contract.run(platform_repo(repo, infra.head(), image=other).path, infra.path)
    assert outcome.status == "failed"
    assert outcome.lines == [
        f"the test job's PostgreSQL is {other}, but the lab's"
        f" (docs/reference/versions.md) is {LAB_POSTGRES}"
    ]


def test_a_workflow_without_the_service_fails(repo: Repo, infra: Repo) -> None:
    platform_repo(repo, infra.head())
    repo.write(".github/workflows/ci.yml", "jobs:\n  test:\n    steps: []\n")
    outcome = contract.run(repo.path, infra.path)
    assert outcome.lines == [
        ".github/workflows/ci.yml names no image for the test job's postgres service"
    ]


@pytest.mark.parametrize(
    ("versions", "finding"),
    [
        (None, "docs/reference/versions.md is missing in the pokus-infrastructure checkout"),
        ("| redis | `r` | `7` | `sha256:" + "2" * 64 + "` |\n", "has 0 rows for postgres"),
        (VERSIONS + VERSIONS.splitlines()[-1] + "\n", "has 2 rows for postgres"),
        (
            VERSIONS + "| postgres | docker.io/library/postgres | 18 | sha256:00 |\n",
            "has 2 rows for postgres",
        ),
        ("| postgres | `docker.io/library/postgres` | `18` | `sha256:00` |\n", "is not image"),
    ],
    ids=["missing", "no-row", "two-rows", "two-rows-one-malformed", "malformed"],
)
def test_the_versions_page_must_have_one_postgres_row(
    tmp_path: Path, versions: str | None, finding: str
) -> None:
    root = tmp_path / "root"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text(WORKFLOW.format(image=LAB_POSTGRES))
    infra = tmp_path / "infra"
    (infra / "docs" / "reference").mkdir(parents=True)
    if versions is not None:
        (infra / "docs" / "reference" / "versions.md").write_text(versions)
    findings, shown = contract.database_image(root, infra)
    assert shown == []
    assert len(findings) == 1
    assert finding in findings[0]


@pytest.mark.parametrize("text", ["ced2fbd", "x" * 40, "a" * 40 + "\n" + "b" * 40, "A" * 40])
def test_the_pin_is_one_full_lowercase_hash(repo: Repo, infra: Repo, text: str) -> None:
    outcome = contract.run(platform_repo(repo, text).path, infra.path)
    assert outcome.status == "failed"
    assert outcome.lines == ["INFRA_PIN: not one full commit hash on one line"]


def test_a_checkout_without_origin_main_fails(repo: Repo, infra: Repo) -> None:
    infra.git("update-ref", "-d", "refs/remotes/origin/main")
    outcome = contract.run(platform_repo(repo, infra.head()).path, infra.path)
    assert outcome.status == "failed"
    assert outcome.lines == ["the pokus-infrastructure checkout has no origin/main: fetch it"]


@pytest.mark.parametrize(
    "line",
    ["pyyaml == 6.0.3", "PyYAML==6.0.3 ; python_version >= '3.12'", "pyyaml==6.0.3"],
)
def test_the_pyyaml_pin_in_its_forms(tmp_path: Path, line: str) -> None:
    (tmp_path / "pyproject.toml").write_text(
        f'[dependency-groups]\ndev = ["{line}"]\n', encoding="utf-8"
    )
    assert contract.infra_pyyaml(tmp_path) == "6.0.3"


def test_the_checkout_must_be_at_the_pin(repo: Repo, infra: Repo) -> None:
    pin = infra.head()
    infra.commit("Later")
    outcome = contract.run(platform_repo(repo, pin).path, infra.path)
    assert outcome.status == "failed"
    assert outcome.lines == [
        f"the pokus-infrastructure checkout is at {infra.head()}, not at INFRA_PIN {pin}"
    ]


def test_the_pin_must_be_on_main(repo: Repo, infra: Repo) -> None:
    infra.git("checkout", "-q", "-b", "side")
    pin = infra.commit("Not on main")
    outcome = contract.run(platform_repo(repo, pin).path, infra.path)
    assert outcome.lines == [f"INFRA_PIN {pin} is not on pokus-infrastructure's main"]


def test_a_schema_error_fails(repo: Repo, infra: Repo) -> None:
    outcome = contract.run(platform_repo(repo, infra.head(), "name: 3\n").path, infra.path)
    assert outcome.status == "failed"
    assert outcome.lines == ["schema: name: 3 is not of type 'string'"]


def test_the_validator_s_refusal_fails(repo: Repo, infra: Repo) -> None:
    platform_repo(repo, infra.head()).write("platform/refuse", "")
    outcome = contract.run(repo.path, infra.path)
    assert outcome.status == "failed"
    assert outcome.lines == [
        "the validator refused the release (exit status 65):",
        "  services[0]: refused",
    ]


def test_another_pyyaml_fails(repo: Repo, infra: Repo) -> None:
    infra.write("pyproject.toml", '[dependency-groups]\ndev = ["PyYAML==0.0.1"]\n')
    infra.git("add", "-A")
    infra.commit("Another PyYAML")
    infra.git("push", "-q", "origin", "main")
    infra.git("fetch", "-q", "origin")
    outcome = contract.run(platform_repo(repo, infra.head()).path, infra.path)
    assert outcome.lines == [
        f"PyYAML {metadata.version('pyyaml')} here, but pokus-infrastructure pins 0.0.1"
    ]


def test_only_the_release_s_files_are_validated(repo: Repo, infra: Repo) -> None:
    # An ignored development environment below platform/ is not staged.
    platform_repo(repo, infra.head()).write(".gitignore", ".venv/\n")
    repo.write("platform/.venv/refuse", "")
    assert contract.run(repo.path, infra.path).status == "passed"


def commit_schema(
    infra: Repo, schema: dict[str, Any], path: str = "contract/principals.schema.json"
) -> None:
    infra.write(path, json.dumps(schema))
    infra.git("add", "-A")
    infra.commit("A schema change")
    infra.git("push", "-q", "origin", "main")
    infra.git("fetch", "-q", "origin")


def test_a_model_that_differs_from_the_contract_fails(repo: Repo, infra: Repo) -> None:
    schema = Principals.model_json_schema(by_alias=True)
    schema["properties"]["new_field"] = {"type": "string"}
    schema["required"] = [*schema["required"], "new_field"]
    commit_schema(infra, schema)
    outcome = contract.run(platform_repo(repo, infra.head()).path, infra.path)
    assert outcome.status == "failed"
    assert outcome.lines[0] == (
        "potka-core's model of principals.json differs from "
        "contract/principals.schema.json: /new_field: only in the contract"
    )
    assert outcome.lines[1].startswith(
        "contract/principals.schema.json is not the schema potka-core's model of"
        " principals.json was written for (sha256 "
    )
    assert len(outcome.lines) == 2


def test_any_other_change_of_a_schema_fails_by_its_sha256(repo: Repo, infra: Repo) -> None:
    schema = Principals.model_json_schema(by_alias=True)
    schema["$defs"]["Socket"]["properties"]["mode"] = {"const": "0666", "type": "string"}
    commit_schema(infra, schema)
    outcome = contract.run(platform_repo(repo, infra.head()).path, infra.path)
    assert outcome.status == "failed"
    assert len(outcome.lines) == 1
    assert outcome.lines[0].endswith(
        ": check the model against it, then update the expected sha256"
    )


FINGERPRINT_SCHEMA = "contract/records/fingerprint.schema.json"
GOLDEN_ENTRY = "a golden image's entry in the fingerprint"


def golden_entry_schema() -> dict[str, Any]:
    return stand_in(next(model for model in contract.MODELS if model.part == "golden_image"))


def test_a_model_of_a_definition_is_compared_with_that_definition(repo: Repo, infra: Repo) -> None:
    schema = golden_entry_schema()
    entry = schema["$defs"]["golden_image"]
    assert sorted(entry["properties"]) == ["fingerprint", "nesting", "packages", "type"]
    assert sorted(entry["required"]) == ["fingerprint", "nesting", "packages", "type"]
    entry["properties"]["update_source"] = {"type": "string"}
    del entry["properties"]["nesting"]
    entry["required"] = ["fingerprint", "type"]
    schema["$defs"]["Package"]["properties"]["status"] = {"type": "string"}
    # A change outside the definition and what it refers to is no difference of the model's.
    schema["properties"] = {"hash": {"type": "string"}}
    commit_schema(infra, schema, FINGERPRINT_SCHEMA)
    outcome = contract.run(platform_repo(repo, infra.head()).path, infra.path)
    assert outcome.status == "failed"
    differs = f"potka-core's model of {GOLDEN_ENTRY} differs from golden_image in"
    assert outcome.lines[:-1] == [
        f"{differs} {FINGERPRINT_SCHEMA}: /nesting: only in the model",
        f"{differs} {FINGERPRINT_SCHEMA}: /update_source: only in the contract",
        f"{differs} {FINGERPRINT_SCHEMA}: /packages: required only by the model",
        f"{differs} {FINGERPRINT_SCHEMA}: /packages[]/status: only in the contract",
    ]
    assert outcome.lines[-1].startswith(
        f"{FINGERPRINT_SCHEMA} is not the schema potka-core's model of"
        f" {GOLDEN_ENTRY} was written for (sha256 "
    )


def test_a_change_outside_the_definition_fails_by_the_file_s_sha256(
    repo: Repo, infra: Repo
) -> None:
    schema = golden_entry_schema()
    schema["properties"] = {"hash": {"type": "string"}}
    commit_schema(infra, schema, FINGERPRINT_SCHEMA)
    outcome = contract.run(platform_repo(repo, infra.head()).path, infra.path)
    assert outcome.status == "failed"
    assert len(outcome.lines) == 1
    assert outcome.lines[0].startswith(
        f"{FINGERPRINT_SCHEMA} is not the schema potka-core's model of"
        f" {GOLDEN_ENTRY} was written for (sha256 "
    )


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object"},
        {"$defs": {"other": {}}},
        {"$defs": {"golden_image": "x"}},
        {"$defs": []},
        {"$defs": "golden_image"},
        [],
    ],
)
def test_a_schema_without_the_definition_fails(repo: Repo, infra: Repo, schema: Any) -> None:
    commit_schema(infra, schema, FINGERPRINT_SCHEMA)
    outcome = contract.run(platform_repo(repo, infra.head()).path, infra.path)
    assert outcome.status == "failed"
    assert outcome.lines == [f"{FINGERPRINT_SCHEMA} has no definition golden_image"]


def test_a_missing_schema_fails_with_the_other_findings(repo: Repo, infra: Repo) -> None:
    (infra.path / "contract/endpoints.schema.json").unlink()
    infra.git("add", "-A")
    infra.commit("No endpoints schema")
    infra.git("push", "-q", "origin", "main")
    infra.git("fetch", "-q", "origin")
    platform_repo(repo, infra.head()).write("platform/refuse", "")
    outcome = contract.run(repo.path, infra.path)
    assert outcome.status == "failed"
    assert outcome.lines[0].startswith("the validator refused the release")
    assert outcome.lines[-1].startswith("contract/endpoints.schema.json cannot be read: ")


def test_the_other_checks_do_not_import_potka_core() -> None:
    code = (
        "import sys; import potka_ci.__main__, potka_ci.gate;"
        " assert not [m for m in sys.modules if m.startswith('potka_core')]"
    )
    ci = Path(__file__).resolve().parents[1]
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=ci, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr


THEIRS: dict[str, Any] = {
    "type": "object",
    "required": ["a", "list"],
    "properties": {
        "a": {"type": "string"},
        "list": {
            "type": "array",
            "items": {"type": "object", "required": ["x"], "properties": {"x": {}, "y": {}}},
        },
    },
}


@pytest.mark.parametrize(
    ("ours", "drift"),
    [
        (THEIRS, []),
        ({**THEIRS, "required": ["a"]}, ["/list: required only by the contract"]),
        (
            {**THEIRS, "properties": {**THEIRS["properties"], "b": {}}},
            ["/b: only in the model"],
        ),
        (
            {
                **THEIRS,
                "properties": {
                    "a": {"anyOf": [{"$ref": "#/$defs/A"}, {"type": "null"}]},
                    "list": {"type": "array", "items": {"$ref": "#/$defs/Item"}},
                },
            },
            ["/list[]/y: only in the contract"],
        ),
        ({**THEIRS, "required": ["a", "list", "b"]}, []),
        (
            {**THEIRS, "properties": {**THEIRS["properties"], "b": {}}, "required": ["a", "b"]},
            ["/b: only in the model", "/list: required only by the contract"],
        ),
    ],
)
def test_schema_drift(ours: dict[str, Any], drift: list[str]) -> None:
    defs = {"A": {"type": "string"}, "Item": {"required": ["x"], "properties": {"x": {}}}}
    assert contract.schema_drift(THEIRS, {**ours, "$defs": defs}) == drift


def test_schema_drift_follows_references_nulls_and_maps_on_both_sides() -> None:
    inner = {"type": "object", "required": ["x"], "properties": {"x": {}}}
    theirs = {
        "type": "object",
        "properties": {
            "opt": {"$ref": "#/$defs/Inner"},
            "map": {"type": "object", "additionalProperties": {"$ref": "#/$defs/Inner"}},
        },
        "$defs": {"Inner": inner},
    }
    other = {"type": "object", "required": ["x", "z"], "properties": {"x": {}, "z": {}}}
    ours = {
        "type": "object",
        "properties": {
            "opt": {"anyOf": [{"$ref": "#/$defs/Other"}, {"type": "null"}]},
            "map": {"type": "object", "additionalProperties": other},
        },
        "$defs": {"Other": other},
    }
    assert contract.schema_drift(theirs, ours) == [
        "/map/*/z: only in the model",
        "/opt/z: only in the model",
    ]
    with pytest.raises(KeyError):
        contract.schema_drift(theirs, {"properties": {"opt": {"$ref": "#/$defs/Missing"}}})
