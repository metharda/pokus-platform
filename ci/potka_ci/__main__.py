"""Command line: ``python -m potka_ci <check> [options]``.

The privacy, references and commit checks read this repository, or with
``--root`` another checkout, by the same rules: the rules are this
repository's files, and the tree, the history and the range are the other
checkout's. A relative path is taken from the working directory; a path
that is this repository is refused, so that a wrong path cannot check this
repository in another's place. The commit check takes that repository's
root commit with ``--root-commit``.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from functools import partial
from pathlib import Path

from potka_ci import (
    build,
    command_path,
    commits,
    contract,
    gate,
    generated,
    incus_api,
    privacy,
    references,
    todos,
)
from potka_ci.outcome import Outcome
from potka_ci.push import SHA_RE, run_on_push
from potka_ci.repo import GitError, git

ROOT = Path(__file__).resolve().parents[2]
# The checks that read a checkout and nothing else of this repository but their rules.
ELSEWHERE = ("privacy", "references", "commits")


def _commit(text: str) -> str:
    if not SHA_RE.fullmatch(text):
        raise argparse.ArgumentTypeError("not a commit hash (40 hex digits)")
    return text


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="potka_ci",
        description=(
            "The repository's checks. The privacy and commit checks read the branch and"
            " the range from the environment (see potka_ci.push)."
        ),
    )
    checks = parser.add_subparsers(dest="check", required=True, metavar="CHECK")
    p = checks.add_parser("privacy", help="no personal or installation-specific data")
    r = checks.add_parser("references", help="no reference outside the product's own terms")
    c = checks.add_parser("commits", help="every commit of the range is the maintainer's")
    for elsewhere in (p, r, c):
        elsewhere.add_argument(
            "--root",
            type=Path,
            help="the checkout to check instead of this repository (the top of its working tree)",
        )
    c.add_argument(
        "--root-commit",
        type=_commit,
        help="the root commit of the repository checked (this repository's when not given)",
    )
    k = checks.add_parser("contract", help="the release against pokus-infrastructure's contract")
    k.add_argument("--infra", type=Path, required=True, help="pokus-infrastructure at INFRA_PIN")
    b = checks.add_parser("build", help="pokus-infrastructure's build of the release")
    b.add_argument("--infra", type=Path, required=True, help="pokus-infrastructure at INFRA_PIN")
    b.add_argument("--uv", default="uv", help="the uv executable")
    b.add_argument("--python", required=True, help="the Python version the build uses (X.Y.Z)")
    n = checks.add_parser("generated", help="the committed generated files are up to date")
    n.add_argument("--infra", type=Path, help="pokus-infrastructure at INFRA_PIN")
    n.add_argument(
        "--incus",
        type=Path,
        help="the directory with Incus's two files; without it the extract is not compared",
    )
    n.add_argument(
        "--write",
        choices=["schemas", "tools", "primitives", "golden-images", "incus-api"],
        help="write the files of one kind instead of comparing every file",
    )
    i = checks.add_parser("incus-sources", help="fetch Incus's two files of the API extract")
    i.add_argument("destination", type=Path, help="the directory to write them into")
    checks.add_parser("command-path", help="no shell where a command is made or carried")
    checks.add_parser("verify-todos", help="list the open verification markers")
    g = checks.add_parser("gate", help="every check once, with one summary line")
    g.add_argument("--infra", type=Path, help="pokus-infrastructure at INFRA_PIN")
    g.add_argument("--incus", type=Path, help="the directory with Incus's two files")
    g.add_argument("--uv", default="uv", help="the uv executable")
    g.add_argument("--python", required=True, help="the Python version the build uses (X.Y.Z)")
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


def _checkout(given: Path | None) -> Path:
    """This repository, or the checkout given: the top of another git working tree.

    Raises:
        GitError: The path given is not the top of a git working tree (with
            git's own refusal, when git gave one), or it is this repository.
    """
    if given is None:
        return ROOT
    root = given.resolve()
    refusal = f"--root: {given} is not the top of a git working tree"
    try:
        top = Path(git(root, ["rev-parse", "--show-toplevel"]).rstrip("\n")).resolve()
    except GitError as exc:
        raise GitError(f"{refusal} ({exc})") from exc
    if top != root:
        raise GitError(refusal)
    if root == ROOT.resolve():
        raise GitError(f"--root: {given} is this repository, which is checked without --root")
    return root


def _elsewhere(args: argparse.Namespace) -> Outcome:
    """Run the privacy, references or commit check on the checkout the arguments name."""
    try:
        root = _checkout(args.root)
    except GitError as exc:
        return Outcome("failed", [str(exc)])
    if args.check == "privacy":
        return run_on_push(root, os.environ, privacy.run)
    if args.check == "references":
        return references.run(root)
    if args.root_commit is None:
        return run_on_push(root, os.environ, commits.run)
    return run_on_push(root, os.environ, partial(commits.run, root_commit=args.root_commit))


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
        incus = args.incus.resolve() if args.incus else None
        ok = gate.run(ROOT, infra, args.uv, args.python, args.verbose, incus=incus)
        return 0 if ok else 1
    if args.check in ELSEWHERE:
        outcome = _elsewhere(args)
    elif args.check == "contract":
        outcome = contract.run(ROOT, args.infra.resolve())
    elif args.check == "build":
        outcome = build.run(ROOT, args.infra.resolve(), args.uv, args.python)
    elif args.check == "generated":
        infra = args.infra.resolve() if args.infra else None
        incus = args.incus.resolve() if args.incus else None
        if args.write:
            outcome = generated.write(ROOT, args.write, infra, incus)
        else:
            # Two checks. Without Incus's files only the extract's is skipped: it is
            # reported by its name, and the status stays that of the other files.
            files = _report("generated", generated.run(ROOT, infra))
            compared = _report("incus-extract", generated.extract(ROOT, incus))
            return 1 if 1 in (files, compared) else files
    elif args.check == "incus-sources":
        outcome = incus_api.fetch(ROOT, args.destination.resolve())
    elif args.check == "command-path":
        outcome = command_path.run(ROOT)
    else:
        outcome = todos.run(ROOT)
        # make verify-todos fails while any marker remains (the gate warns).
        status = _report(args.check, outcome)
        return 1 if outcome.status == "warning" else status
    return _report(args.check, outcome)


if __name__ == "__main__":
    sys.exit(main())
