"""The contract check: the release against pokus-infrastructure at INFRA_PIN.

It checks, with a checkout of pokus-infrastructure:

* ``INFRA_PIN`` is one full commit hash, the checkout is at that commit, and
  the commit is on the project's ``main`` (``origin/main``);
* ``platform-manifest.yaml`` is valid against the manifest schema there
  (``contract/platform-manifest.schema.json``, with jsonschema);
* the project's own validator accepts the staged release
  (``python -m pinka_platform validate <release> --no-host-check``);
* the PyYAML this check runs with is the version the project pins.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from importlib import metadata
from pathlib import Path

import jsonschema
import yaml

from potka_ci.outcome import Outcome
from potka_ci.repo import GitError, git, git_ok, stage_release

PIN_FILE = "INFRA_PIN"
PIN_RE = re.compile(r"[0-9a-f]{40}")
SCHEMA = Path("contract") / "platform-manifest.schema.json"
VALIDATOR_PATH = Path("tools") / "platform"
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
    pinned = infra_pyyaml(infra)
    ours = metadata.version("pyyaml")
    if pinned != ours:
        findings.append(f"PyYAML {ours} here, but pokus-infrastructure pins {pinned}")
    else:
        lines.append(f"PyYAML {ours}, as pokus-infrastructure pins")
    if findings:
        return Outcome("failed", findings)
    return Outcome("passed", lines)
