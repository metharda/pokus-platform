"""The build check: pokus-infrastructure's build of the release, reproduced.

pokus-infrastructure builds a release from its ``uv.lock`` in phases: the
locked third-party wheels first (with the network, building nothing), then
the release itself offline, without build isolation, and then each agent
package's own environment, offline, with a last pass that removes the build
backend from it again. This check runs those phases on a staged copy of the
release with the project's own flags and phase arguments, imported from its
code at ``INFRA_PIN`` (``pinka_platform.deploy``), then checks that the build
changed no file of the release and that the project's validator accepts the
built release (``built=True``: every command exists, and each agent's
environment holds only its package and dependencies).

The checkout must be at ``INFRA_PIN``. The phases run without the caller's
``UV_*`` variables, with the ones the project sets for its build. The
offline phases run with ``UV_OFFLINE=1`` and ``--offline``; unlike on a lab
host, they do not run in a network namespace.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from types import ModuleType

from potka_ci.contract import PIN_FILE, read_pin
from potka_ci.outcome import Outcome
from potka_ci.repo import GitError, git, stage_release

VALIDATOR_PATH = Path("tools") / "platform"


def _modules(infra: Path) -> dict[str, ModuleType]:
    path = str(infra / VALIDATOR_PATH)
    if path not in sys.path:
        sys.path.insert(0, path)
    names = ("deploy", "release", "agentpkg", "validate", "contract")
    return {n: importlib.import_module(f"pinka_platform.{n}") for n in names}


def has_build_group(stage: Path, group: str) -> bool:
    """Whether the release's pyproject.toml has a non-empty dependency group GROUP.

    Args:
        stage: The staged release.
        group: The group's name.

    Returns:
        True when the group exists and is not empty.
    """
    try:
        data = tomllib.loads((stage / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    groups = data.get("dependency-groups", {})
    return isinstance(groups, dict) and bool(groups.get(group))


def phases(m: dict[str, ModuleType], stage: Path) -> list[tuple[str, dict[str, str], list[str]]]:
    """The build's phases: ``(name, extra environment, uv sync arguments)``.

    As pokus-infrastructure composes them: the build group only when the
    release has one, and then a last pass per agent that removes it again.

    Args:
        m: pokus-infrastructure's modules.
        stage: The staged release.

    Returns:
        The phases in order.
    """
    deploy, release, agentpkg, contract = m["deploy"], m["release"], m["agentpkg"], m["contract"]
    caps = contract.load(contract.DEFAULT_DIR).caps
    build_group = has_build_group(stage, release.BUILD_GROUP)
    group = ["--group", release.BUILD_GROUP] if build_group else []
    offline = {"UV_OFFLINE": "1"}
    steps: list[tuple[str, dict[str, str], list[str]]] = [
        ("fetch", {}, ["--all-packages", *deploy.UV_FETCH, *group]),
        ("build", offline, ["--all-packages", *deploy.UV_BUILD, *group]),
    ]
    for service, dist in deploy.agent_packages(stage, int(caps["manifest_bytes"])):
        env = {**offline, "UV_PROJECT_ENVIRONMENT": str(stage / agentpkg.env_dir(service))}
        steps.append((f"agent {service}", env, ["--package", dist, *deploy.UV_BUILD, *group]))
        if build_group:
            steps.append(
                (f"agent {service} prune", env, ["--package", dist, *deploy.UV_AGENT_PRUNE])
            )
    return steps


def run(root: Path, infra: Path | None, uv: str = "uv", python: str = "3.12") -> Outcome:
    """Build the staged release as pokus-infrastructure does, and check it.

    Args:
        root: The working tree.
        infra: The pokus-infrastructure checkout at the pin, or None.
        uv: The uv executable.
        python: The Python the build uses (a version or a path).

    Returns:
        The outcome (skipped without a checkout).
    """
    if infra is None:
        return Outcome("skipped", ["no pokus-infrastructure checkout (INFRA=<path>)"])
    pin = read_pin(root)
    try:
        head = git(infra, ["rev-parse", "HEAD"]).strip()
    except GitError as exc:
        return Outcome("failed", [f"{infra}: not a pokus-infrastructure checkout: {exc}"])
    if pin is None or head != pin:
        return Outcome(
            "failed", [f"the pokus-infrastructure checkout is at {head}, not at {PIN_FILE} {pin}"]
        )
    try:
        m = _modules(infra)
    except (ImportError, AttributeError) as exc:
        return Outcome("failed", [f"{infra}: cannot import its platform tools: {exc}"])
    deploy, validate, contract = m["deploy"], m["validate"], m["contract"]
    lines: list[str] = []
    with tempfile.TemporaryDirectory(prefix="potka-build-") as tmp:
        stage = stage_release(root, Path(tmp) / "release")
        before = deploy.snapshot(stage)
        base = {
            "UV_CACHE_DIR": str(Path(tmp) / "uv-cache"),
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_NO_CONFIG": "1",
        }
        for name, extra, args in phases(m, stage):
            argv = [uv, *deploy.UV_SYNC, *args, "--python", python]
            inherited = {k: v for k, v in os.environ.items() if not k.startswith("UV_")}
            done = subprocess.run(
                argv,
                cwd=stage,
                env={**inherited, **base, **extra},
                capture_output=True,
                text=True,
                check=False,
            )
            shown = " ".join(argv[1:])
            if done.returncode != 0:
                output = (done.stdout + done.stderr).rstrip().splitlines()
                return Outcome(
                    "failed",
                    [*lines, f"{name}: uv {shown}: exit status {done.returncode}", *output],
                )
            lines.append(f"{name}: uv {shown}")
        after = deploy.snapshot(stage)
        changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        findings = [f"the build changed or removed {k}" for k in changed]
        result = validate.validate_release(
            stage, contract.load(contract.DEFAULT_DIR), None, built=True
        )
        findings += [f"the validator refused the built release: {p}" for p in result.problems]
    if findings:
        return Outcome("failed", [*lines, *findings])
    return Outcome("passed", [*lines, "the built release is unchanged and valid (built=True)"])
