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

from potka_ci import contract, incus_api, todos

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
VERSIONS = """| Release | Package | Version |
| --- | --- | --- |
| ubuntu-24.04 | incus | `1:9.9.9-ubuntu24.04-1` (x86_64), `1:9.9.9-ubuntu24.04-2` (aarch64) |
| debian-13 | incus | `1:9.9.9-debian13-202601010000` |
| debian-13 | docker-ce | `5:29.8.2-1~debian.13~trixie` |

| Name | Image | Tag | Digest | Platforms |
| --- | --- | --- | --- | --- |
| otel_collector | `docker.io/otel/collector` | `0.1` | `sha256:{other}` | linux/amd64 |
| postgres | `docker.io/library/postgres` | `18.6-trixie` | `sha256:{digest}` | linux/amd64 |
| opa | `docker.io/example-org/opa` | `9.9.9-static` | `sha256:{other}` | linux/amd64 |

| Component | Version | Machine | URL | sha256 |
| --- | --- | --- | --- | --- |
| opa | `9.9.9` | x86_64 | `https://example.org/opa/v9.9.9/opa_amd64` | `{amd}` |
| opa | `9.9.9` | aarch64 | `https://example.org/opa/v9.9.9/opa_arm64` | `{arm}` |
""".format(other="1" * 64, digest=LAB_POSTGRES.rsplit(":", 1)[1], amd="3" * 64, arm="4" * 64)
POSTGRES_ROW = next(line for line in VERSIONS.splitlines() if line.startswith("| postgres |"))
OPA_LOCK = (
    "# A comment.\n"
    f"9.9.9 aarch64 https://example.org/opa/v9.9.9/opa_arm64 {'4' * 64}\n"
    f"9.9.9 x86_64 https://example.org/opa/v9.9.9/opa_amd64 {'3' * 64}\n"
)
OPA_LINE = (
    "the policy tests' OPA is the lab's (docs/reference/versions.md): 9.9.9 for aarch64, x86_64"
)
WORKFLOW = "jobs:\n  test:\n    services:\n      postgres:\n        image: {image}\n"
INCUS_SOURCE = {
    "repository": "https://github.com/example-org/incus",
    "tag": "v9.9.9",
    "version": "9.9.9",
    "commit": "c" * 40,
    "license": "Apache-2.0",
    "files": {incus_api.API: "a" * 64, incus_api.CONFIGURATION: "b" * 64},
}
INCUS_LINE = "the Incus API extract is of Incus 9.9.9, as docs/reference/versions.md pins (2 rows)"
DEBIAN = "`1:9.9.9-debian13-202601010000`"
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
    repo.write(incus_api.SOURCE.as_posix(), json.dumps(INCUS_SOURCE))
    repo.write(contract.OPA_LOCK.as_posix(), OPA_LOCK)
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
    assert OPA_LINE in outcome.lines
    assert outcome.lines[-2] == f"PyYAML {metadata.version('pyyaml')}, as pokus-infrastructure pins"
    assert outcome.lines[-1] == INCUS_LINE


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
        (VERSIONS + POSTGRES_ROW + "\n", "has 2 rows for postgres"),
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


@pytest.mark.parametrize(
    ("versions", "source", "finding"),
    [
        (VERSIONS, INCUS_SOURCE, None),
        (
            VERSIONS.replace("1:9.9.9-debian13", "1:9.9.10-debian13"),
            INCUS_SOURCE,
            "the Incus API extract is of Incus 9.9.9, but docs/reference/versions.md pins 9.9.10,"
            " 9.9.9: make a new extract (platform/tests/fixtures/incus-api/source.json,"
            " make incus-api)",
        ),
        (
            VERSIONS.replace("| incus |", "| incus-client |"),
            INCUS_SOURCE,
            "docs/reference/versions.md has no row of the package incus to compare the extract's"
            " Incus with",
        ),
        (
            None,
            INCUS_SOURCE,
            "docs/reference/versions.md is missing in the pokus-infrastructure checkout",
        ),
        (
            b"\xff\xfe| debian-13 | incus | `1:9.9.9-1` |\n",
            INCUS_SOURCE,
            "docs/reference/versions.md of the pokus-infrastructure checkout is not UTF-8",
        ),
        # A row whose version the check cannot read fails it, even beside rows it can read.
        (
            VERSIONS.replace(DEBIAN, "to be verified"),
            INCUS_SOURCE,
            "docs/reference/versions.md: the incus version of debian-13: to be verified"
            " is not one this check can read",
        ),
        (
            VERSIONS.replace(DEBIAN, "`1:9.9.9~rc1-1`"),
            INCUS_SOURCE,
            "docs/reference/versions.md: the incus version of debian-13: `1:9.9.9~rc1-1`"
            " is not one this check can read",
        ),
        (
            VERSIONS.replace(DEBIAN, "`1:9.9.9-1`, `nine`"),
            INCUS_SOURCE,
            "docs/reference/versions.md: the incus version of debian-13: `1:9.9.9-1`, `nine`"
            " is not one this check can read",
        ),
        (
            VERSIONS.replace(DEBIAN, ""),
            INCUS_SOURCE,
            "docs/reference/versions.md: the incus version of debian-13: an empty cell"
            " is not one this check can read",
        ),
        # What stands outside the quotes is unread too: a value that is not verified yet,
        # alone or beside one that is; a version written without them; an architecture
        # without its version.
        (
            VERSIONS.replace(DEBIAN, todos.MARKER),
            INCUS_SOURCE,
            f"docs/reference/versions.md: the incus version of debian-13: {todos.MARKER}"
            " is not one this check can read",
        ),
        (
            VERSIONS.replace(DEBIAN, f"`1:9.9.9-1` (x86_64), {todos.MARKER} (aarch64)"),
            INCUS_SOURCE,
            "docs/reference/versions.md: the incus version of debian-13: `1:9.9.9-1` (x86_64),"
            f" {todos.MARKER} (aarch64) is not one this check can read",
        ),
        (
            VERSIONS.replace(DEBIAN, "`1:9.9.9-1` (x86_64), (aarch64)"),
            INCUS_SOURCE,
            "docs/reference/versions.md: the incus version of debian-13: `1:9.9.9-1` (x86_64),"
            " (aarch64) is not one this check can read",
        ),
        (
            VERSIONS.replace(DEBIAN, "`1:9.9.9-1` (x86_64), 1:8.8.8-1 (aarch64)"),
            INCUS_SOURCE,
            "docs/reference/versions.md: the incus version of debian-13: `1:9.9.9-1` (x86_64),"
            " 1:8.8.8-1 (aarch64) is not one this check can read",
        ),
        # Read as the same version: no epoch, no revision; a package whose name only holds
        # the word and a later column are not read at all.
        (VERSIONS.replace(DEBIAN, "`9.9.9`"), INCUS_SOURCE, None),
        (VERSIONS.replace(DEBIAN, "`9.9.9-1` (x86_64), `1:9.9.9-2` (aarch64)"), INCUS_SOURCE, None),
        (
            VERSIONS.replace(
                "| debian-13 | docker-ce |",
                "| debian-13 | python3-incus | `1:8.8.8-1` |\n| debian-13 | docker-ce |",
            ),
            INCUS_SOURCE,
            None,
        ),
        (VERSIONS.replace(DEBIAN + " |", DEBIAN + " | `1:8.8.8-1` |"), INCUS_SOURCE, None),
        (
            VERSIONS,
            {**INCUS_SOURCE, "files": {}},
            "platform/tests/fixtures/incus-api/source.json: files are not doc/rest-api.yaml and"
            " internal/server/metadata/configuration.json",
        ),
    ],
    ids=[
        "the-same",
        "another-in-one-row",
        "no-row",
        "no-page",
        "not-utf-8",
        "no-version",
        "a-release-candidate",
        "one-of-two-unread",
        "an-empty-cell",
        "not-verified",
        "one-of-two-not-verified",
        "one-of-two-without-a-version",
        "one-of-two-not-quoted",
        "no-epoch-no-revision",
        "two-architectures",
        "another-package",
        "a-later-column",
        "no-source",
    ],
)
def test_the_extract_s_incus_must_be_the_lab_s(
    tmp_path: Path, versions: str | bytes | None, source: dict[str, Any], finding: str | None
) -> None:
    root, infra = tmp_path / "root", tmp_path / "infra"
    (root / incus_api.DIRECTORY).mkdir(parents=True)
    (root / incus_api.SOURCE).write_text(json.dumps(source))
    (infra / "docs" / "reference").mkdir(parents=True)
    page = infra / "docs" / "reference" / "versions.md"
    if isinstance(versions, bytes):
        page.write_bytes(versions)
    elif versions is not None:
        page.write_text(versions)
    findings, shown = contract.incus_version(root, infra)
    assert (findings, shown) == (([finding], []) if finding else ([], [INCUS_LINE]))


def _opa(tmp_path: Path, versions: str | bytes | None, lock: str | None) -> tuple[Path, Path]:
    root, infra = tmp_path / "root", tmp_path / "infra"
    (root / contract.OPA_LOCK).parent.mkdir(parents=True)
    if lock is not None:
        (root / contract.OPA_LOCK).write_text(lock)
    (infra / "docs" / "reference").mkdir(parents=True)
    page = infra / "docs" / "reference" / "versions.md"
    if isinstance(versions, bytes):
        page.write_bytes(versions)
    elif versions is not None:
        page.write_text(versions)
    return root, infra


def test_the_lock_s_opa_is_the_lab_s(tmp_path: Path) -> None:
    assert contract.opa_binary(*_opa(tmp_path, VERSIONS, OPA_LOCK)) == ([], [OPA_LINE])


AMD = f"9.9.9 x86_64 https://example.org/opa/v9.9.9/opa_amd64 {'3' * 64}"
ARM = f"9.9.9 aarch64 https://example.org/opa/v9.9.9/opa_arm64 {'4' * 64}"


@pytest.mark.parametrize(
    ("lock", "findings"),
    [
        (
            OPA_LOCK.replace("3" * 64, "5" * 64),
            [
                "platform/tests/opa/opa.lock is not the lab's OPA (docs/reference/versions.md):",
                f"  the lock: {AMD.replace('3' * 64, '5' * 64)}",
                f"  the page: {AMD}",
            ],
        ),
        (
            OPA_LOCK.replace("9.9.9 x86_64", "9.9.8 x86_64"),
            [
                "platform/tests/opa/opa.lock is not the lab's OPA (docs/reference/versions.md):",
                f"  the lock: {AMD.replace('9.9.9 x', '9.9.8 x')}",
                f"  the page: {AMD}",
            ],
        ),
        (
            OPA_LOCK.replace("opa_amd64", "opa_other"),
            [
                "platform/tests/opa/opa.lock is not the lab's OPA (docs/reference/versions.md):",
                f"  the lock: {AMD.replace('opa_amd64', 'opa_other')}",
                f"  the page: {AMD}",
            ],
        ),
        (
            ARM + "\n",
            [
                "platform/tests/opa/opa.lock is not the lab's OPA (docs/reference/versions.md):",
                f"  the page: {AMD}",
            ],
        ),
        (
            OPA_LOCK + AMD.replace("3" * 64, "5" * 64) + "\n",
            ["platform/tests/opa/opa.lock names a machine twice for opa"],
        ),
    ],
    ids=["sha256", "version", "url", "a-machine-less", "a-machine-twice"],
)
def test_a_lock_that_differs_from_the_page_fails(
    tmp_path: Path, lock: str, findings: list[str]
) -> None:
    assert contract.opa_binary(*_opa(tmp_path, VERSIONS, lock)) == (findings, [])


@pytest.mark.parametrize(
    ("versions", "lock", "finding"),
    [
        (
            None,
            OPA_LOCK,
            "docs/reference/versions.md is missing in the pokus-infrastructure checkout",
        ),
        (b"\xff\xfe", OPA_LOCK, "docs/reference/versions.md of the pokus-infrastructure checkout"),
        (
            "| opa | `docker.io/example-org/opa` | `9.9.9-static` | `sha256:" + "1" * 64 + "` |\n",
            OPA_LOCK,
            "docs/reference/versions.md has no row of a binary for opa",
        ),
        (VERSIONS, None, "platform/tests/opa/opa.lock is missing or is not UTF-8"),
        (VERSIONS, "# Only a comment.\n", "every line that is no comment is version, machine"),
        (
            VERSIONS,
            OPA_LOCK + "9.9.9 x86_64\n",
            "every line that is no comment is version, machine",
        ),
        (VERSIONS, OPA_LOCK.replace(" x86_64 ", "  x86_64 "), "every line that is no comment is"),
        (VERSIONS, OPA_LOCK.replace("https://", "http://"), "every line that is no comment is"),
        (VERSIONS, OPA_LOCK.replace("3" * 64, "3" * 63), "every line that is no comment is"),
        (
            VERSIONS + VERSIONS.splitlines()[-1].replace("4" * 64, "6" * 64) + "\n",
            OPA_LOCK,
            "docs/reference/versions.md names a machine twice for opa",
        ),
    ],
    ids=[
        "no-page",
        "page-not-utf-8",
        "only-the-image's-row",
        "no-lock",
        "an-empty-lock",
        "a-short-line",
        "two-spaces",
        "not-https",
        "a-short-sha256",
        "the-page-twice",
    ],
)
def test_a_page_or_a_lock_that_cannot_be_compared_fails(
    tmp_path: Path, versions: str | bytes | None, lock: str | None, finding: str
) -> None:
    findings, shown = contract.opa_binary(*_opa(tmp_path, versions, lock))
    assert shown == []
    assert len(findings) == 1
    assert finding in findings[0]


def test_the_shipped_lock_has_one_line_for_each_of_two_machines_at_one_version() -> None:
    platform = Path(__file__).resolve().parents[2] / "platform"
    lock = (platform / "tests" / "opa" / "opa.lock").read_text(encoding="utf-8")
    rows = [contract.OPA_LOCK_RE.match(line) for line in lock.splitlines() if line[:1] != "#"]
    assert None not in rows
    assert sorted(row["machine"] for row in rows if row is not None) == ["aarch64", "x86_64"]
    assert len({row["version"] for row in rows if row is not None}) == 1


def test_a_versions_page_that_is_missing_or_not_utf_8_is_reported_once(
    repo: Repo, infra: Repo
) -> None:
    root = platform_repo(repo, infra.git("rev-parse", "HEAD")).path
    page = infra.path / "docs" / "reference" / "versions.md"
    page.write_bytes(b"\xff\xfe| debian-13 | incus | `1:9.9.9-1` |\n")
    outcome = contract.run(root, infra.path)
    unread = "docs/reference/versions.md of the pokus-infrastructure checkout is not UTF-8"
    assert (outcome.status, outcome.lines.count(unread)) == ("failed", 1)
    page.unlink()
    outcome = contract.run(root, infra.path)
    missing = "docs/reference/versions.md is missing in the pokus-infrastructure checkout"
    assert (outcome.status, outcome.lines.count(missing)) == ("failed", 1)


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
