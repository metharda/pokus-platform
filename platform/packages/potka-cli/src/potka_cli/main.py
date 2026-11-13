"""The ``potka`` command.

``potka catalog lint [PATH]`` checks a catalog checkout (the current
directory when PATH is not given) and prints one line per finding, as
``<file or directory>: <rule>: <what is wrong>``, then the number of
findings, or ``no finding``; with ``--json`` it prints one JSON object,
``{"findings": [{"path", "rule", "message"}, ...]}``, instead. Both go to
standard output.

Exit status: 0 when the catalog has no finding, 1 when it has (a path that
is missing or is no catalog is a finding), 2 when the command line is not
understood.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from potka_core.catalog.lint import lint


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="potka", description="The command line of the Pokus Platform."
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    catalog = commands.add_parser("catalog", help="work on a catalog checkout")
    actions = catalog.add_subparsers(dest="action", required=True, metavar="ACTION")
    lint_parser = actions.add_parser(
        "lint",
        help="check a catalog checkout",
        description=(
            "Check a catalog checkout: its layout and the form of each of its files and,"
            " once every file has its form, what the files say about each other. Exit"
            " status: 0 without a finding, 1 with findings (a path that is no catalog is"
            " one), 2 for a command line that is not understood."
        ),
    )
    lint_parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path(),
        metavar="PATH",
        help="the catalog's root (default: the current directory)",
    )
    lint_parser.add_argument(
        "--json", action="store_true", help="print the findings as one JSON object"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command.

    Args:
        argv: The arguments (the process's own when None).

    Returns:
        The exit status (the module's docstring). A command line that is
        not understood ends the process with status 2 instead.
    """
    args = _parser().parse_args(argv)
    findings = lint(args.path)
    if args.json:
        sys.stdout.write(json.dumps({"findings": [asdict(f) for f in findings]}, sort_keys=True))
        sys.stdout.write("\n")
    else:
        for finding in findings:
            sys.stdout.write(f"{finding.path}: {finding.rule}: {finding.message}\n")
        sys.stdout.write(f"{len(findings)} finding(s)\n" if findings else "no finding\n")
    return 1 if findings else 0
