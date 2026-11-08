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

Like pokus-infrastructure's build, every phase gets an interpreter's path,
not a version: the version is resolved first, in the caller's environment
(where uv finds its own installations, before the phases drop the ``UV_*``
variables), with ``uv python find``, and the interpreter must report exactly
that version.
"""

from __future__ import annotations

import importlib
import os
import re
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
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
# What the interpreter reports: its version, and whether it is a virtual
# environment's.
PROBE = "import sys; print('%d.%d.%d' % sys.version_info[:3], sys.prefix != sys.base_prefix)"


class InterpreterError(Exception):
    """The build's Python cannot be resolved to a fitting interpreter.

    Attributes:
        output: What the failed command printed, line by line.
    """

    def __init__(self, message: str, output: list[str] | None = None) -> None:
        """Keep the message and the failed command's output.

        Args:
            message: What failed.
            output: The command's output, line by line.
        """
        super().__init__(message)
        self.output = output or []


def interpreter(uv: str, version: str, cwd: Path) -> str:
    """The absolute path of the Python VERSION the build runs with.

    uv looks it up (``uv python find VERSION``, uv-managed installations
    included) in the caller's environment, with its ``UV_*`` variables, but
    without ``VIRTUAL_ENV`` and from CWD, outside any project: inside one,
    uv returns the project's virtual environment, and the build, like
    pokus-infrastructure's, takes a base interpreter.

    Args:
        uv: The uv executable.
        version: The Python version, in full (``X.Y.Z``).
        cwd: A directory outside any project.

    Returns:
        The interpreter's absolute path.

    Raises:
        InterpreterError: The version is not in full, cannot be resolved,
            or resolves to an interpreter of another version or a virtual
            environment's.
    """
    if not VERSION_RE.fullmatch(version):
        raise InterpreterError(f"the build Python must be a full version (X.Y.Z), not '{version}'")
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    command = [uv, "python", "find", version]
    shown = " ".join(command)
    try:
        found = subprocess.run(
            command, cwd=cwd, env=env, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise InterpreterError(f"Python {version} cannot be resolved: {shown}: {exc}") from exc
    if found.returncode != 0:
        raise InterpreterError(
            f"Python {version} cannot be resolved: {shown}: exit status {found.returncode}",
            (found.stdout + found.stderr).rstrip().splitlines(),
        )
    path = found.stdout.strip()
    if not os.path.isabs(path):
        raise InterpreterError(f"Python {version}: {shown} printed no absolute path: '{path}'")
    try:
        probe = subprocess.run(
            [path, "-I", "-c", PROBE], cwd=cwd, env=env, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise InterpreterError(f"Python {version}: {path} cannot run: {exc}") from exc
    reported = probe.stdout.split()
    if probe.returncode != 0 or len(reported) != 2:
        raise InterpreterError(f"Python {version}: {path} does not report its version")
    if reported[0] != version:
        raise InterpreterError(f"Python {version}: {path} reports Python {reported[0]}")
    if reported[1] != "False":
        raise InterpreterError(
            f"Python {version}: {path} is a virtual environment's; the build takes a base"
            " interpreter"
        )
    return path


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


def run(root: Path, infra: Path | None, uv: str = "uv", python: str = "") -> Outcome:
    """Build the staged release as pokus-infrastructure does, and check it.

    Args:
        root: The working tree.
        infra: The pokus-infrastructure checkout at the pin, or None.
        uv: The uv executable.
        python: The Python version the build uses, in full (``X.Y.Z``).

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
        try:
            path = interpreter(uv, python, Path(tmp))
        except InterpreterError as exc:
            return Outcome("failed", [str(exc), *exc.output])
        lines.append(f"python {python}: {path}")
        stage = stage_release(root, Path(tmp) / "release")
        before = deploy.snapshot(stage)
        base = {
            "UV_CACHE_DIR": str(Path(tmp) / "uv-cache"),
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_NO_CONFIG": "1",
        }
        for name, extra, args in phases(m, stage):
            argv = [uv, *deploy.UV_SYNC, *args, "--python", path]
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
