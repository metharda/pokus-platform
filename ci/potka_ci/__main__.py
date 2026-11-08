"""Command line: ``python -m potka_ci <check> [options]``."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from potka_ci import build, commits, contract, gate, privacy, references, todos
from potka_ci.outcome import Outcome
from potka_ci.push import run_on_push

ROOT = Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="potka_ci",
        description=(
            "The repository's checks. The privacy and commit checks read the branch and"
            " the range from the environment (see potka_ci.push)."
        ),
    )
    checks = parser.add_subparsers(dest="check", required=True, metavar="CHECK")
    checks.add_parser("privacy", help="no personal or installation-specific data")
    checks.add_parser("references", help="no reference outside the product's own terms")
    checks.add_parser("commits", help="every commit of the range is the maintainer's")
    k = checks.add_parser("contract", help="the release against pokus-infrastructure's contract")
    k.add_argument("--infra", type=Path, required=True, help="pokus-infrastructure at INFRA_PIN")
    b = checks.add_parser("build", help="pokus-infrastructure's build of the release")
    b.add_argument("--infra", type=Path, required=True, help="pokus-infrastructure at INFRA_PIN")
    b.add_argument("--uv", default="uv", help="the uv executable")
    b.add_argument("--python", default="3.12", help="the Python the build uses")
    checks.add_parser("verify-todos", help="list the open verification markers")
    g = checks.add_parser("gate", help="every check once, with one summary line")
    g.add_argument("--infra", type=Path, help="pokus-infrastructure at INFRA_PIN")
    g.add_argument("--uv", default="uv", help="the uv executable")
    g.add_argument("--python", default="3.12", help="the Python the build uses")
    g.add_argument("--verbose", action="store_true", help="print every step's output")
    return parser


def _report(name: str, outcome: Outcome) -> int:
    for line in outcome.lines:
        print(line)
    if os.environ.get("GITHUB_ACTIONS") == "true" and outcome.status in ("warning", "failed"):
        kind = "warning" if outcome.status == "warning" else "error"
        print(f"::{kind} title={name}::{name} {outcome.status}: see the job's log")
    if outcome.status == "skipped":
        print(f"{name}: skipped", file=sys.stderr)
    return outcome.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    """Run one check.

    Args:
        argv: The arguments (the process's own when None).

    Returns:
        The exit status: 0 passed (warnings included), 1 failed, 2 skipped;
        verify-todos fails (1) while a marker remains.
    """
    args = _parser().parse_args(argv)
    if args.check == "gate":
        infra = args.infra.resolve() if args.infra else None
        ok = gate.run(ROOT, infra, args.uv, args.python, args.verbose)
        return 0 if ok else 1
    if args.check == "privacy":
        outcome = run_on_push(ROOT, os.environ, privacy.run)
    elif args.check == "references":
        outcome = references.run(ROOT)
    elif args.check == "commits":
        outcome = run_on_push(ROOT, os.environ, commits.run)
    elif args.check == "contract":
        outcome = contract.run(ROOT, args.infra.resolve())
    elif args.check == "build":
        outcome = build.run(ROOT, args.infra.resolve(), args.uv, args.python)
    else:
        outcome = todos.run(ROOT)
        # make verify-todos fails while any marker remains (the gate warns).
        status = _report(args.check, outcome)
        return 1 if outcome.status == "warning" else status
    return _report(args.check, outcome)


if __name__ == "__main__":
    sys.exit(main())
