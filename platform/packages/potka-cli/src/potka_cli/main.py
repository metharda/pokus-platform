"""The ``potka`` command.

``potka catalog lint [PATH]`` checks a catalog checkout (the current
directory when PATH is not given) and prints one line per finding, as
``<file or directory>: <rule>: <what is wrong>``, then the number of
findings, or ``no finding``; with ``--json`` it prints one JSON object,
``{"findings": [{"path", "rule", "message"}, ...]}``, instead. Both go to
standard output.

Some rules compare the catalog with the golden images table of the
release the command is installed in (``infra-data/golden-images.json``);
``--infra-data DIR`` reads the table from another directory. Others
compare it with the tool registry of that release (``tools/registry``);
``--tools DIR`` reads the registry in another directory. One compares each
step's parameters with the library of fault primitives and probes of that
release.

Exit status: 0 when the catalog has no finding, 1 when it has (a path that
is missing or is no catalog is a finding), 2 when the command line is not
understood or the golden images table or the tool registry cannot be read.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from potka_core.catalog.lint import Library, StepView, ToolView, lint
from potka_core.images import TABLE, load_table
from potka_primitives import library as steps
from potka_primitives.base import Step
from potka_tools.registry import FORBIDDEN, REGISTRY, Registry, RegistryError, Tool, load_registry
from potka_tools.template import standalone
from potka_tools.values import below, normalize
from pydantic import JsonValue, ValidationError

# What is wrong with a value for a tool's argument, as a finding says it.
_SAID = {
    "wrong_type": "is not of the argument's type",
    "not_allowed": "is not one of the argument's values",
    "out_of_range": "is not within the argument's bounds",
    "bad_form": "has not the argument's form",
}


def _view(tool: Tool) -> ToolView:
    templates = [said.argv for said in (tool.command, tool.post_check) if said is not None]
    alone = frozenset[str]().union(*(standalone(template) for template in templates))

    def problem(name: str, value: JsonValue) -> str | None:
        arg = tool.args.get(name)
        if arg is None:
            return "is no argument of the tool"
        code = arg.problem(value)
        if code is not None:
            return _SAID[code]
        if arg.within is not None:
            normal = normalize(str(value))
            if normal is None or not below(normal, arg.within):
                return "is not below a root the tool allows"
        # A value that is a whole element of the tool's command: no call can give one
        # that would read as an option.
        if name in alone and str(value).startswith("-"):
            return "begins with a dash where the tool's command takes a whole word"
        return None

    return ToolView(
        mutating=tool.mutating,
        forbidden=tool.risk == FORBIDDEN,
        categories=tool.applicable_categories,
        arguments=frozenset(tool.args),
        problem=problem,
    )


def tool_views(registry: Registry) -> dict[str, ToolView]:
    """The tools of REGISTRY as the catalog lint sees them, by name."""
    return {tool.name: _view(tool) for tool in registry.tools}


# What is wrong with a step's parameter, by the kind of the model's error.
_UNFIT = {
    "missing": "is missing",
    "extra_forbidden": "is no parameter of it",
    "string_pattern_mismatch": "has not the parameter's form",
    "string_too_long": "is longer than the parameter may be",
    "string_too_short": "is shorter than the parameter may be",
    "too_long": "has more items than the parameter may have",
    "too_short": "has fewer items than the parameter must have",
    "greater_than_equal": "is not within the parameter's bounds",
    "less_than_equal": "is not within the parameter's bounds",
    "literal_error": "is not one of the parameter's values",
}
_KEY = re.compile(r"[a-z_][a-z0-9_]{0,62}")


def _unfit(error: Mapping[str, Any]) -> str:
    """One error of a parameter model, in words that hold no value."""
    kind = str(error.get("type", ""))
    if kind == "value_error":
        # The models' own words, which name a rule and never what was given.
        said = str(error.get("msg", "")).removeprefix("Value error, ")
    elif kind.endswith("_type"):
        said = "is not of the parameter's type"
    else:
        said = _UNFIT.get(kind, "does not fit the parameter")
    # A place is a key or an index; a key is shown only in the form of a parameter's name.
    place = ".".join(
        str(part) if isinstance(part, int) or _KEY.fullmatch(str(part)) else "(a key)"
        for part in error.get("loc", ())
    )
    return f"{place}: {said}" if place else said


def _step_view(step: type[Step[Any]]) -> StepView:
    def problem(params: Mapping[str, JsonValue]) -> tuple[str, ...]:
        try:
            step.parameters(params)
        except ValidationError as refused:
            errors = refused.errors(include_url=False, include_context=False, include_input=False)
            return tuple(sorted({_unfit(error) for error in errors}))
        return ()

    return StepView(problem=problem, service=step.requires.service)


def step_views(primitives: Iterable[type[Step[Any]]], probes: Iterable[type[Step[Any]]]) -> Library:
    """Fault primitives and probes as the catalog lint sees them."""
    return Library(
        primitives={step.name: _step_view(step) for step in primitives},
        probes={step.name: _step_view(step) for step in probes},
    )


def library_views() -> Library:
    """The release's library of fault primitives and probes, as the catalog lint sees it."""
    return step_views(steps.PRIMITIVES.values(), steps.PROBES.values())


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
            " one), 2 for a command line that is not understood or a golden images table"
            " or a tool registry that cannot be read."
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
    lint_parser.add_argument(
        "--infra-data",
        type=Path,
        metavar="DIR",
        help=f"the directory that holds {TABLE.name} (default: the release's {TABLE.parent}/)",
    )
    lint_parser.add_argument(
        "--tools",
        type=Path,
        metavar="DIR",
        help=f"the tool registry's directory (default: the release's {REGISTRY}/)",
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
    try:
        table = load_table(args.infra_data)
    except (OSError, ValueError) as exc:
        # Never the file's content: the reason only.
        reason = "it is not such a table"
        if isinstance(exc, OSError):
            reason = exc.strerror or type(exc).__name__
        sys.stderr.write(
            f"potka: the golden images table ({TABLE.name}) cannot be read: {reason}\n"
        )
        return 2
    try:
        registry = load_registry(args.tools)
    except RegistryError as exc:
        sys.stderr.write("potka: the tool registry cannot be read:\n")
        for path, reason in exc.problems:
            sys.stderr.write(f"  {path}: {reason}\n")
        return 2
    findings = lint(args.path, tool_views(registry), library_views(), table)
    if args.json:
        sys.stdout.write(json.dumps({"findings": [asdict(f) for f in findings]}, sort_keys=True))
        sys.stdout.write("\n")
    else:
        for finding in findings:
            sys.stdout.write(f"{finding.path}: {finding.rule}: {finding.message}\n")
        sys.stdout.write(f"{len(findings)} finding(s)\n" if findings else "no finding\n")
    return 1 if findings else 0
