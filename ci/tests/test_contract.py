from __future__ import annotations

import json
from importlib import metadata
from pathlib import Path

import pytest
from conftest import Repo

from potka_ci import contract

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["name"],
    "properties": {"name": {"type": "string"}},
}
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


@pytest.fixture
def infra(tmp_path: Path) -> Repo:
    """A stand-in pokus-infrastructure checkout with origin/main."""
    r = Repo(tmp_path / "infra")
    r.write("contract/platform-manifest.schema.json", json.dumps(SCHEMA))
    r.write("tools/platform/pinka_platform/__init__.py", "")
    r.write("tools/platform/pinka_platform/__main__.py", FAKE_VALIDATOR)
    pin = f"pyyaml=={metadata.version('pyyaml')}"
    r.write("pyproject.toml", f'[dependency-groups]\ndev = ["{pin}"]\n')
    r.git("add", "-A")
    r.commit("The contract")
    bare = tmp_path / "infra.git"
    r.git("init", "-q", "--bare", str(bare))
    r.git("remote", "add", "origin", str(bare))
    r.git("push", "-q", "origin", "main")
    r.git("fetch", "-q", "origin")
    return r


def platform_repo(repo: Repo, pin: str, manifest: str = "name: potka\n") -> Repo:
    repo.write("INFRA_PIN", pin + "\n")
    repo.write("platform/platform-manifest.yaml", manifest)
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
    assert outcome.lines[-1] == f"PyYAML {metadata.version('pyyaml')}, as pokus-infrastructure pins"


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
