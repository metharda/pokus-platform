"""The gate: every check once, quiet, with one summary line.

It runs the linters, the tests and the repository's checks from the
``platform/`` directory and prints one line with the counts of passed,
failed, skipped and warnings (naming the skipped steps, with pytest's own
summary), then the complete output of every failed step, the reason of every
skipped step and every warning. With ``verbose`` it prints every step's
output. A step without its input (no pokus-infrastructure checkout) is
skipped, never passed; a step that raises fails, and the gate goes on with
the next one. Tests that pytest skipped, or warnings it printed, make the
pytest step a warning.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from potka_ci import build, commits, contract, privacy, references, todos
from potka_ci.outcome import Outcome
from potka_ci.push import run_on_push

WORKFLOW = Path(".github") / "workflows" / "ci.yml"
# pytest's last line: "117 passed, 1 skipped, 2 warnings in 3.04s".
PYTEST_SUMMARY_RE = re.compile(r"^=*\s*(?P<counts>\d+ \w+(?:, \d+ \w+)*) in [\d.]+s")
PYTEST_NOTEWORTHY = ("skipped", "xfailed", "xpassed", "warning", "warnings", "deselected")


@dataclass(frozen=True)
class Step:
    """One step of the gate.

    Attributes:
        name: Its name in the output.
        outcome: What it found.
    """

    name: str
    outcome: Outcome


def tool(name: str, argv: list[str], cwd: Path) -> Outcome:
    """Run a linter or the tests.

    Args:
        name: The executable.
        argv: Its arguments.
        cwd: Where to run it.

    Returns:
        Passed or failed, with its output.
    """
    path = shutil.which(name)
    if path is None:
        return Outcome("failed", [f"{name} is not installed: run make setup"])
    done = subprocess.run([path, *argv], cwd=cwd, capture_output=True, text=True, check=False)
    output = (done.stdout + done.stderr).rstrip().splitlines()
    return Outcome("passed" if done.returncode == 0 else "failed", output)


def pytest_outcome(outcome: Outcome) -> Outcome:
    """Turn a passing pytest run with skips or warnings into a warning.

    Args:
        outcome: pytest's outcome as :func:`tool` returned it.

    Returns:
        The outcome, a warning when pytest skipped a test or printed a warning.
    """
    counts = pytest_counts(outcome)
    if outcome.status == "passed" and any(
        word in PYTEST_NOTEWORTHY for word in counts.replace(",", "").split()
    ):
        return Outcome("warning", outcome.lines)
    return outcome


def pytest_counts(outcome: Outcome) -> str:
    """Pytest's summary counts ("117 passed, 1 skipped"), or "no summary".

    Args:
        outcome: pytest's outcome.

    Returns:
        The counts.
    """
    for line in reversed(outcome.lines):
        m = PYTEST_SUMMARY_RE.match(line.strip())
        if m:
            return m.group("counts")
    return "no summary"


def guarded(check: Callable[[], Outcome]) -> Outcome:
    """Run a step; an exception fails it with its traceback.

    Args:
        check: The step.

    Returns:
        Its outcome.
    """
    try:
        return check()
    except Exception:
        return Outcome("failed", traceback.format_exc().rstrip().splitlines())


def run(
    root: Path,
    infra: Path | None,
    uv: str = "uv",
    python: str = "3.12",
    verbose: bool = False,
    printer: Callable[[str], None] = print,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Run every step and print the summary.

    Args:
        root: The working tree.
        infra: The pokus-infrastructure checkout at the pin, or None.
        uv: The uv executable (for the build check).
        python: The Python the build check uses.
        verbose: Print every step's output.
        printer: Where the lines go.
        environ: The environment the privacy and commit checks read the
            branch and the range from (the process's own when None).

    Returns:
        True when no step failed.
    """
    platform = root / "platform"
    code = ["packages", "tests", "../ci"]
    mypy_paths = [str(p.relative_to(platform)) for p in sorted(platform.glob("packages/*/src"))]
    workflow = root / WORKFLOW
    env = os.environ if environ is None else environ
    steps: list[tuple[str, Callable[[], Outcome]]] = [
        ("ruff", lambda: tool("ruff", ["check", *code], platform)),
        ("ruff-format", lambda: tool("ruff", ["format", "--check", *code], platform)),
        ("mypy", lambda: tool("mypy", [*mypy_paths, "tests", "../ci"], platform)),
        (
            "actionlint",
            lambda: (
                tool("actionlint", [str(workflow)], root)
                if workflow.is_file()
                else Outcome("skipped", [f"no {WORKFLOW.as_posix()}"])
            ),
        ),
        (
            "pytest",
            lambda: pytest_outcome(tool("pytest", ["-q", "-p", "no:cacheprovider"], platform)),
        ),
        ("contract", lambda: contract.run(root, infra)),
        ("build", lambda: build.run(root, infra, uv, python)),
        ("privacy", lambda: run_on_push(root, env, privacy.run)),
        ("references", lambda: references.run(root)),
        ("commits", lambda: run_on_push(root, env, commits.run)),
        ("verify-todos", lambda: todos.run(root)),
    ]
    done = [Step(name, guarded(check)) for name, check in steps]
    counts = {
        s: sum(1 for d in done if d.outcome.status == s)
        for s in ("passed", "failed", "skipped", "warning")
    }
    skipped = [d.name for d in done if d.outcome.status == "skipped"]
    pytest = next((pytest_counts(d.outcome) for d in done if d.name == "pytest"), "not run")
    printer(
        f"gate: {counts['passed']} passed, {counts['failed']} failed, {counts['skipped']} skipped"
        + (f" ({', '.join(skipped)})" if skipped else "")
        + f", {counts['warning']} warnings ({len(done)} steps; pytest: {pytest})"
    )
    for d in done:
        if d.outcome.status == "failed" or verbose:
            printer(f"--- {d.name}: {d.outcome.status}")
            for line in d.outcome.lines:
                printer(line)
        elif d.outcome.status in ("skipped", "warning"):
            for line in d.outcome.lines:
                printer(f"{d.outcome.status}: {d.name}: {line}")
    return counts["failed"] == 0
