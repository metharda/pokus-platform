"""The contract check: the release against pokus-infrastructure at INFRA_PIN.

It checks, with a checkout of pokus-infrastructure:

* ``INFRA_PIN`` is one full commit hash, the checkout is at that commit, and
  the commit is on the project's ``main`` (``origin/main``);
* ``platform-manifest.yaml`` is valid against the manifest schema there
  (``contract/platform-manifest.schema.json``, with jsonschema);
* the project's own validator accepts the staged release
  (``python -m pinka_platform validate <release> --no-host-check``);
* potka-core's models of the files infra writes (``principals.json``,
  ``endpoints.toml``) have the field names and required fields of the
  files' schemas there, and each schema is the one the model was written
  for (its sha256, recorded here): any other change of a schema at a new
  pin fails until the model is checked against it and the sha256 updated;
* the PostgreSQL service of the workflow's ``test`` job is the lab's server:
  the image, tag and digest of the postgres row of
  ``docs/reference/versions.md`` there;
* the PyYAML this check runs with is the version the project pins.

potka-core is imported only by the model comparison, so the other checks
run without it.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from importlib import metadata
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from potka_ci.outcome import Outcome
from potka_ci.repo import GitError, git, git_ok, stage_release

PIN_FILE = "INFRA_PIN"
PIN_RE = re.compile(r"[0-9a-f]{40}")
SCHEMA = Path("contract") / "platform-manifest.schema.json"
VALIDATOR_PATH = Path("tools") / "platform"
# potka-core's models of the files infra writes: the file, its schema, the
# model ("module:class") and the sha256 of the schema the model was written for.
MODELS: tuple[tuple[str, Path, str, str], ...] = (
    (
        "principals.json",
        Path("contract") / "principals.schema.json",
        "potka_core.principals:Principals",
        "88752c2da679abdfaa2035b770ae56dd25a9ca74c10f6fc0676955d5e2fcdde2",
    ),
    (
        "endpoints.toml",
        Path("contract") / "endpoints.schema.json",
        "potka_core.endpoints:Endpoints",
        "ac0b3647ad8580da6e68a49e63a3291b5a78450e14e8599d5ced092c4c181275",
    ),
)
WORKFLOW = Path(".github") / "workflows" / "ci.yml"
VERSIONS = Path("docs") / "reference" / "versions.md"
VERSIONS_POSTGRES_RE = re.compile(r"^\|\s*postgres\s*\|")
VERSIONS_ROW_RE = re.compile(
    r"^\|\s*postgres\s*\|\s*`(?P<image>[^`\s]+)`\s*\|\s*`(?P<tag>[^`\s]+)`\s*\|"
    r"\s*`(?P<digest>sha256:[0-9a-f]{64})`\s*\|"
)
PYYAML_PIN_RE = re.compile(r"^pyyaml\s*==\s*(?P<version>[^\s;]+)\s*(?:;.*)?$", re.IGNORECASE)


def read_pin(root: Path) -> str | None:
    """The pinned commit, or None when the file is not one full hash.

    Args:
        root: The working tree.

    Returns:
        The hash.
    """
    text = (root / PIN_FILE).read_text(encoding="utf-8")
    pin = text.removesuffix("\n")
    return pin if PIN_RE.fullmatch(pin) else None


def infra_pyyaml(infra: Path) -> str | None:
    """The PyYAML version pokus-infrastructure pins in its pyproject.toml.

    Args:
        infra: The checkout.

    Returns:
        The version, or None when no exact pin is found.
    """
    data = tomllib.loads((infra / "pyproject.toml").read_text(encoding="utf-8"))
    groups = data.get("dependency-groups", {})
    requirements = [r for group in groups.values() for r in group if isinstance(r, str)]
    requirements += data.get("project", {}).get("dependencies", [])
    for requirement in requirements:
        m = PYYAML_PIN_RE.match(requirement.strip())
        if m:
            return m.group("version")
    return None


def schema_errors(manifest_path: Path, schema_path: Path) -> list[str]:
    """Every JSON Schema error of the manifest.

    Args:
        manifest_path: The manifest.
        schema_path: The schema.

    Returns:
        One line per error: its place and message.
    """
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
    )
    return [
        f"schema: {'/'.join(str(p) for p in error.absolute_path) or 'manifest'}: {error.message}"
        for error in sorted(validator.iter_errors(manifest), key=lambda e: list(e.absolute_path))
    ]


def _node(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """SCHEMA with a reference followed, and an optional value's ``null`` left out."""
    if "$ref" in schema:
        return _node(defs[schema["$ref"].rsplit("/", 1)[-1]], defs)
    options = [o for o in schema.get("anyOf", []) if o.get("type") != "null"]
    if len(options) == 1:
        return _node(options[0], defs)
    return schema


def schema_drift(theirs: dict[str, Any], ours: dict[str, Any], where: str = "") -> list[str]:
    """Where a model's JSON Schema has other fields than the contract's.

    At every level: the property names and which of them are required;
    then the same inside arrays' items and maps' values. Types, patterns,
    enumerations and constants are not compared, nor the options of a union
    of two or more types: the schema's sha256 (`model_check`) covers them.

    Args:
        theirs: The contract's schema (or a part of it), with its ``$defs``.
        ours: The model's schema (or the matching part), with its ``$defs``.
        where: The path of this part, for the messages.

    Returns:
        One line per difference.
    """
    return _drift(theirs, ours, theirs.get("$defs", {}), ours.get("$defs", {}), where)


def _drift(
    theirs: dict[str, Any],
    ours: dict[str, Any],
    their_defs: dict[str, Any],
    our_defs: dict[str, Any],
    where: str,
) -> list[str]:
    theirs, ours = _node(theirs, their_defs), _node(ours, our_defs)
    drift: list[str] = []
    mine, contract = ours.get("properties"), theirs.get("properties")
    if mine is not None or contract is not None:
        mine, contract = mine or {}, contract or {}
        for name in sorted(set(mine) ^ set(contract)):
            side = "the contract" if name in contract else "the model"
            drift.append(f"{where}/{name}: only in {side}")
        required = set(theirs.get("required", [])) ^ set(ours.get("required", []))
        for name in sorted(required & set(mine) & set(contract)):
            side = "the contract" if name in theirs.get("required", []) else "the model"
            drift.append(f"{where}/{name}: required only by {side}")
        for name in sorted(set(mine) & set(contract)):
            drift += _drift(contract[name], mine[name], their_defs, our_defs, f"{where}/{name}")
    for key, suffix in (("items", "[]"), ("additionalProperties", "/*")):
        if isinstance(theirs.get(key), dict) and isinstance(ours.get(key), dict):
            drift += _drift(theirs[key], ours[key], their_defs, our_defs, f"{where}{suffix}")
    return drift


def model_check(
    infra: Path, name: str, schema: Path, target: str, digest: str
) -> tuple[list[str], list[str]]:
    """Compare potka-core's model of NAME with its schema in the checkout.

    Args:
        infra: The pokus-infrastructure checkout.
        name: The file the model reads.
        schema: The file's schema, relative to the checkout.
        target: The model, as ``module:class``.
        digest: The sha256 of the schema the model was written for.

    Returns:
        The findings, and the lines to print when there is none.
    """
    try:
        raw = (infra / schema).read_bytes()
        contract_schema = json.loads(raw)
    except (OSError, ValueError) as exc:
        return [f"{schema.as_posix()} cannot be read: {exc}"], []
    module, _, attribute = target.partition(":")
    try:
        model = getattr(importlib.import_module(module), attribute)
    except (ImportError, AttributeError) as exc:
        return [f"potka-core's model of {name} ({target}) cannot be imported: {exc}"], []
    drift = schema_drift(contract_schema, model.model_json_schema(by_alias=True))
    findings = [
        f"potka-core's model of {name} differs from {schema.as_posix()}: {d}" for d in drift
    ]
    actual = hashlib.sha256(raw).hexdigest()
    if actual != digest:
        findings.append(
            f"{schema.as_posix()} is not the schema potka-core's model of {name} was written for"
            f" (sha256 {actual}, expected {digest}): check the model against it,"
            " then update the expected sha256"
        )
    if findings:
        return findings, []
    return [], [
        f"potka-core's model of {name} has the fields of {schema.as_posix()},"
        f" the schema it was written for (sha256 {digest[:12]})"
    ]


def database_image(root: Path, infra: Path) -> tuple[list[str], list[str]]:
    """Whether the ``test`` job's PostgreSQL service is the lab's server.

    Args:
        root: The working tree (its workflow).
        infra: The pokus-infrastructure checkout (its versions page).

    Returns:
        The findings and the lines to show.
    """
    try:
        text = (infra / VERSIONS).read_text(encoding="utf-8")
    except OSError:
        return [f"{VERSIONS.as_posix()} is missing in the pokus-infrastructure checkout"], []
    rows = [line.strip() for line in text.splitlines() if VERSIONS_POSTGRES_RE.match(line.strip())]
    if len(rows) != 1:
        return [f"{VERSIONS.as_posix()} has {len(rows)} rows for postgres, not one"], []
    match = VERSIONS_ROW_RE.match(rows[0])
    if match is None:
        return [f"{VERSIONS.as_posix()}'s postgres row is not image, tag and digest"], []
    lab = f"{match['image']}:{match['tag']}@{match['digest']}"
    try:
        workflow = yaml.safe_load((root / WORKFLOW).read_text(encoding="utf-8"))
        image = workflow["jobs"]["test"]["services"]["postgres"]["image"]
    except (OSError, KeyError, TypeError, yaml.YAMLError):
        return [f"{WORKFLOW.as_posix()} names no image for the test job's postgres service"], []
    if image != lab:
        where = VERSIONS.as_posix()
        return [f"the test job's PostgreSQL is {image}, but the lab's ({where}) is {lab}"], []
    return [], [f"the test job's PostgreSQL is the lab's ({VERSIONS.as_posix()}): {lab}"]


def run(root: Path, infra: Path | None) -> Outcome:
    """Check the release against the contract at the pin.

    Args:
        root: The working tree.
        infra: The pokus-infrastructure checkout, or None.

    Returns:
        The outcome (skipped without a checkout).
    """
    if infra is None:
        return Outcome("skipped", ["no pokus-infrastructure checkout (INFRA=<path>)"])
    pin = read_pin(root)
    if pin is None:
        return Outcome("failed", [f"{PIN_FILE}: not one full commit hash on one line"])
    try:
        head = git(infra, ["rev-parse", "HEAD"]).strip()
    except GitError as exc:
        return Outcome("failed", [str(exc)])
    findings: list[str] = []
    lines: list[str] = []
    if head != pin:
        findings.append(f"the pokus-infrastructure checkout is at {head}, not at {PIN_FILE} {pin}")
    if not git_ok(infra, ["rev-parse", "--verify", "--quiet", "origin/main^{commit}"]):
        findings.append("the pokus-infrastructure checkout has no origin/main: fetch it")
    elif not git_ok(infra, ["merge-base", "--is-ancestor", pin, "origin/main"]):
        findings.append(f"{PIN_FILE} {pin} is not on pokus-infrastructure's main")
    else:
        lines.append(f"{PIN_FILE} {pin} is on pokus-infrastructure's main")
    if findings:
        return Outcome("failed", findings)
    with tempfile.TemporaryDirectory(prefix="potka-contract-") as tmp:
        release = stage_release(root, Path(tmp) / "release")
        errors = schema_errors(release / "platform-manifest.yaml", infra / SCHEMA)
        findings += errors
        if not errors:
            lines.append(f"platform-manifest.yaml is valid against {SCHEMA.as_posix()}")
        done = subprocess.run(
            [sys.executable, "-m", "pinka_platform", "validate", str(release), "--no-host-check"],
            cwd=infra,
            env={**os.environ, "PYTHONPATH": str(infra / VALIDATOR_PATH)},
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            findings.append(f"the validator refused the release (exit status {done.returncode}):")
            findings += [f"  {line}" for line in (done.stderr + done.stdout).splitlines()]
        else:
            lines.append("the validator accepts the release:")
            lines += [f"  {line}" for line in done.stdout.splitlines()]
    for name, schema, target, digest in MODELS:
        found, shown = model_check(infra, name, schema, target, digest)
        findings += found
        lines += shown
    found, shown = database_image(root, infra)
    findings += found
    lines += shown
    pinned = infra_pyyaml(infra)
    ours = metadata.version("pyyaml")
    if pinned != ours:
        findings.append(f"PyYAML {ours} here, but pokus-infrastructure pins {pinned}")
    else:
        lines.append(f"PyYAML {ours}, as pokus-infrastructure pins")
    if findings:
        return Outcome("failed", findings)
    return Outcome("passed", lines)
