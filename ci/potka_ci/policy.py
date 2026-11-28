"""The policy bundle: well-formed, formatted, and every line of its decision tried.

The bundle (``platform/policy/``) is checked with OPA itself, the lab's
version (``platform/tests/opa/opa.lock``; ``make opa`` installs it into
``platform/.opa/``):

* ``opa check --strict``: the bundle compiles, with no unused variable or
  import;
* ``opa fmt --list``: every file has OPA's own format;
* ``opa test``: every case passes;
* ``opa test --coverage``: no line of the bundle is left untried. The
  report is read here, not left to ``--threshold``: below a threshold OPA
  prints no report, only that the threshold is not met, and the untried
  lines would stay unnamed.

The binary is run only after its sha256 was compared with the lock's, here
as at its installation. Without the binary the check is skipped; in CI
(``CI=true``), where it must be there, it fails.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from collections.abc import Mapping
from pathlib import Path

from potka_ci.contract import OPA_LOCK, OPA_LOCK_RE
from potka_ci.outcome import Outcome

BINARY = Path("platform") / ".opa" / "opa"
BUNDLE = Path("platform") / "policy"
TIMEOUT = 120
FULL = 100


class NoOpa(Exception):
    """The lab's OPA is not installed, or what is there is not it."""


def locked(root: Path, machine: str | None = None) -> tuple[str, str]:
    """The version and the sha256 the lock names for MACHINE (this one when None).

    Raises:
        NoOpa: The lock is missing or has no line for the machine.
    """
    machine = platform.machine() if machine is None else machine
    try:
        text = (root / OPA_LOCK).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise NoOpa(f"{OPA_LOCK.as_posix()} is missing or is not UTF-8") from exc
    rows = [OPA_LOCK_RE.match(line) for line in text.splitlines()]
    found = [row for row in rows if row is not None and row["machine"] == machine]
    if len(found) != 1:
        raise NoOpa(f"{OPA_LOCK.as_posix()} has {len(found)} lines for {machine}, not one")
    return found[0]["version"], found[0]["sha256"]


def verified_binary(root: Path) -> Path:
    """The installed OPA, after its sha256 was compared with the lock's.

    Returns:
        The binary's path.

    Raises:
        NoOpa: It is not installed, or its sha256 is not the locked one.
    """
    _, sha256 = locked(root)
    binary = root / BINARY
    if not binary.is_file():
        raise NoOpa(f"no OPA in {BINARY.parent.as_posix()}: make opa")
    digest = hashlib.sha256()
    with binary.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    if digest.hexdigest() != sha256:
        raise NoOpa(f"{BINARY.as_posix()} is not the locked binary (its sha256 differs): make opa")
    return binary


def _opa(binary: Path, args: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary), *args],
        cwd=root / "platform",
        capture_output=True,
        # OPA prints UTF-8 (a duration in microseconds, for one), whatever this process's locale.
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=TIMEOUT,
        env={"PATH": os.defpath, "HOME": str(root / BINARY.parent)},
    )


def _said(done: subprocess.CompletedProcess[str]) -> list[str]:
    return [f"  {line}" for line in (done.stdout + done.stderr).splitlines()]


def run(root: Path, environ: Mapping[str, str] | None = None) -> Outcome:
    """Check the bundle of the working tree ROOT with the lab's OPA.

    Args:
        root: The working tree.
        environ: The environment ``CI`` is read from (the process's own
            when None).

    Returns:
        Passed with the number of cases; failed with OPA's own words;
        without the locked binary skipped, and failed in CI.
    """
    env = os.environ if environ is None else environ
    bundle = BUNDLE.relative_to("platform").as_posix()
    try:
        binary = verified_binary(root)
    except NoOpa as exc:
        return Outcome("failed" if env.get("CI") == "true" else "skipped", [str(exc)])
    findings: list[str] = []
    checked = _opa(binary, ["check", "--strict", bundle], root)
    if checked.returncode != 0:
        findings += [
            f"opa check --strict {bundle}: exit status {checked.returncode}",
            *_said(checked),
        ]
    listed = _opa(binary, ["fmt", "--list", bundle], root)
    if listed.returncode != 0 or listed.stdout.strip():
        findings += [
            f"opa fmt --list {bundle}: these files are not in OPA's format",
            *_said(listed),
        ]
    if findings:
        return Outcome("failed", findings)
    tested = _opa(binary, ["test", "--format", "json", bundle], root)
    try:
        cases = json.loads(tested.stdout) if tested.stdout.strip() else []
    except ValueError:
        cases = None
    if not isinstance(cases, list) or not all(isinstance(case, dict) for case in cases):
        return Outcome("failed", [f"opa test {bundle}: no list of cases", *_said(tested)])
    failed = [case for case in cases if case.get("fail") or case.get("error") or case.get("skip")]
    if tested.returncode != 0 or failed or not cases:
        names = [f"  {case.get('package')}.{case.get('name')}: not passed" for case in failed]
        status = f"exit status {tested.returncode}, {len(cases)} case(s)"
        # The cases again as OPA itself reports them, with where each one failed.
        shown = _opa(binary, ["test", bundle], root)
        return Outcome("failed", [f"opa test {bundle}: {status}", *names, *_said(shown)])
    covered = _opa(binary, ["test", "--coverage", "--format", "json", bundle], root)
    report = _coverage(covered.stdout)
    if report is None:
        status = f"exit status {covered.returncode}"
        return Outcome(
            "failed",
            [f"opa test --coverage {bundle}: no coverage report ({status})", *_said(covered)],
        )
    share, untried = report
    if covered.returncode != 0 or untried or share != FULL:
        said = f"exit status {covered.returncode}, coverage {share:g}"
        return Outcome(
            "failed",
            [
                f"opa test --coverage {bundle}: not every line is tried ({said})",
                *untried,
                *[f"  {line}" for line in covered.stderr.splitlines()],
            ],
        )
    return Outcome(
        "passed",
        [f"{bundle}: {len(cases)} case(s) passed, every line tried, in OPA's format, strict"],
    )


def _coverage(report: str) -> tuple[float, list[str]] | None:
    """A coverage report's share of tried lines and its untried lines; None when it is none."""
    try:
        data = json.loads(report)
        share = data["coverage"]
        if isinstance(share, bool) or not isinstance(share, int | float):
            return None
        lines: list[str] = []
        for name, entry in sorted(data["files"].items()):
            rows = sorted(
                {
                    row
                    for span in entry.get("not_covered") or []
                    for row in range(span["start"]["row"], span["end"]["row"] + 1)
                }
            )
            lines += [
                f"  {name}: line {first}" + (f" to {last}" if last != first else "")
                for first, last in _runs(rows)
            ]
        return float(share), lines
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def _runs(rows: list[int]) -> list[tuple[int, int]]:
    """Sorted ROWS as runs of rows that follow one another: the first and the last of each."""
    runs: list[tuple[int, int]] = []
    for row in rows:
        if runs and row == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], row)
        else:
            runs.append((row, row))
    return runs
