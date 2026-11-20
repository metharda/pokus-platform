"""What a registry is compiled to, for those who do not read the registry itself.

* `model_tools`: the tools as a model is given them, each an OpenAI
  function tool with its name, its description and the JSON Schema of its
  arguments. A mutating tool's arguments gain a required ``_meta``, in
  which the model says how confident it is, why it acts and what it
  expects. A schema says nothing of a command, a handler, a source of a
  run's context or a root of a path, and no forbidden tool is among them.
* `policy_tools` and `policy_registry`: what the policy engine is told. Of
  every tool, the forbidden ones too: its version, its risk, whether it is
  mutating and the diagnoses it fits. Of the registry: its version and its
  hash. Two documents, so that no tool's name can meet a key of the
  registry's.
* `documentation`: the page that lists the tools for a reader.

Each is a function of the registry alone: the same registry gives the same
output, the tools in the registry's order, by name.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Collection
from typing import Any, Final

from potka_tools.arguments import META, META_SCHEMA, argument_schema
from potka_tools.registry import FORBIDDEN, PATH_MAX, STRING_MAX, Arg, Registry, Tool
from potka_tools.template import Marked

# What a value with a source must be, in a reader's words.
_SOURCES: Final = {
    "context.nodes": "one of the run's nodes",
    "context.node.services": "a service of the node",
    "context.node.http_endpoints": "an HTTP endpoint of the node, by its name",
    "context.node.readable_paths": "a path of the node that may be read",
    "context.node.patchable_paths": "a path of the node that may be patched",
}
_WHERE: Final = {"node": "in the node", "probe_client": "on the run's probe client"}
# How a page of the documentation begins. No verification run has read the page yet.
FRONT_MATTER: Final = (
    "---",
    "title: The tools a model may call",
    "type: reference",
    "audience: [researcher, scenario-author, developer, reader]",
    'applies_to: {platform: ">=0.0"}',
    "last_verified: {date: null, rc: null, by: null}",
    "---",
)


def _function(tool: Tool) -> dict[str, Any]:
    parameters = argument_schema(tool)
    if tool.mutating:
        parameters["properties"][META] = copy.deepcopy(dict(META_SCHEMA))
        parameters["required"] = [META, *parameters["required"]]
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": parameters,
        },
    }


def model_tools(registry: Registry, names: Collection[str] | None = None) -> list[dict[str, Any]]:
    """The tools of REGISTRY as a model is given them.

    Args:
        registry: The registry.
        names: The tools to give; None for every tool that is not forbidden.

    Returns:
        One OpenAI function tool for each, by name. The schema of a
        mutating tool's arguments has a required ``_meta``.

    Raises:
        ValueError: A name is no tool of the registry, or a forbidden one:
            a model is never given a forbidden tool.
    """
    given = {tool.name: tool for tool in registry.tools if tool.risk != FORBIDDEN}
    if names is not None:
        other = sorted(set(names) - set(given))
        if other:
            raise ValueError(f"not a tool a model may be given: {other[0]!r}")
        given = {name: tool for name, tool in given.items() if name in names}
    return [_function(tool) for tool in given.values()]


def policy_tools(registry: Registry) -> dict[str, dict[str, Any]]:
    """What the policy engine is told of each tool of REGISTRY, by the tool's name.

    Returns:
        For every tool, the forbidden ones too: ``version``, ``risk``,
        ``mutating`` and ``applicable_categories``.
    """
    return {
        tool.name: {
            "version": tool.version,
            "risk": tool.risk,
            "mutating": tool.mutating,
            "applicable_categories": list(tool.applicable_categories),
        }
        for tool in registry.tools
    }


def policy_registry(registry: Registry) -> dict[str, Any]:
    """What the policy engine is told of REGISTRY itself: its ``version`` and its ``hash``."""
    return {"version": registry.version, "hash": registry.hash}


def _line(text: str | None) -> str:
    """Words of an entry on one line, each run of white space as one space."""
    return " ".join((text or "").split())


def _cell(text: str) -> str:
    """TEXT as a table's cell holds it: a bar is escaped, in a code span too."""
    return text.replace("|", "\\|")


def _code(text: object) -> str:
    """TEXT as a code span, as it is: its fence is longer than any run of backticks in it."""
    said = str(text)
    if "`" not in said:
        return f"`{said}`"
    fence = "`" * (max(len(run) for run in re.findall("`+", said)) + 1)
    return f"{fence} {said} {fence}"


def _written(template: tuple[str | Marked, ...]) -> str:
    """A command as its registry file writes it: a JSON list, one element each."""
    shown = [one if isinstance(one, str) else {"literal": one.literal} for one in template]
    return _code(json.dumps(shown, ensure_ascii=True))


def _rule(arg: Arg) -> str:
    """What a value of ARG must be, in words."""
    said = []
    if arg.type == "integer":
        said.append(f"from {arg.minimum} to {arg.maximum}")
    if arg.enum is not None:
        said.append("one of " + ", ".join(_code(value) for value in arg.enum))
    if arg.source is not None:
        said.append(_SOURCES[arg.source])
    if arg.pattern is not None:
        said.append(f"matches {_code(arg.pattern)}")
    if arg.within is not None:
        said.append("a path below " + " or ".join(_code(root) for root in arg.within))
    if arg.max_length is not None:
        said.append(f"at most {arg.max_length} characters")
    return "; ".join(said)


def _entry(tool: Tool) -> list[str]:
    lines = [f"### `{tool.name}`", "", _line(tool.description), ""]
    lines.append(f"- Risk: {tool.risk}. Version of the entry: {tool.version}.")
    if tool.applicable_categories:
        fits = ", ".join(_code(label) for label in tool.applicable_categories)
        lines.append(f"- Fits the diagnoses: {fits}.")
    if tool.command is not None:
        said = tool.command
        lines.append(
            f"- Runs {_WHERE[said.on]}: {_written(said.argv)}. Ended after {said.timeout_s} s"
            f" and killed {said.kill_after_s} s later; at most {said.max_output_bytes} bytes"
            " of each output stream are given back."
        )
    if tool.post_check is not None:
        lines.append(f"- Checked afterwards by: {_written(tool.post_check.argv)}.")
    if tool.handler is not None:
        lines.append(
            f"- Carried out by the handler `{tool.handler}`; at most {tool.max_output_bytes}"
            " bytes are given back."
        )
    lines.append("")
    if not tool.args:
        return [*lines, "No arguments.", ""]
    lines += [
        "| Argument | Type | Required | Rule | Description |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, arg in sorted(tool.args.items()):
        required = "yes" if arg.default is None else f"no; default {_code(arg.default)}"
        cells = (f"`{name}`", arg.type, required, _rule(arg), _line(arg.description))
        lines.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
    return [*lines, ""]


def documentation(registry: Registry) -> str:
    """The page that lists the tools of REGISTRY.

    Returns:
        Markdown: the read-only tools, the mutating ones and the forbidden
        ones, each tool with its risk, what stands behind it and its
        arguments.
    """
    lines = [
        *FRONT_MATTER,
        "",
        "<!-- Written by `make tools` from tools/registry/. Do not edit it by hand. -->",
        "",
        "# The tools a model may call",
        "",
        f"Version {registry.version} of the tool registry, content hash `{registry.hash}`.",
        "",
        "A tool runs one command, element by element as its entry writes it and never through"
        " a shell, or is carried out by a handler of the executing service. Some tools run a"
        " second command afterwards, which shows what the first one did. In a command, a"
        " name in braces is the argument of that name, and an element written as"
        ' `{"literal": ...}` is taken as it is, braces and all.',
        "",
        "A call's arguments are checked against the rules below and against the run's nodes"
        " before anything runs. A string and a path are printable ASCII, of at most"
        f" {STRING_MAX} and {PATH_MAX} characters unless a rule says fewer. A value that is a"
        " whole element of a command is not empty and does not begin with a dash. No path"
        " lies in `/dev`, `/proc` or `/sys`, whatever else allows it. A mutating call also"
        f" carries `{META}`: the model's confidence in the action, its reason and the effect"
        " it expects.",
        "",
    ]
    groups = (
        ("Read-only tools", [t for t in registry.tools if not t.mutating]),
        ("Mutating tools", [t for t in registry.tools if t.mutating and t.risk != FORBIDDEN]),
    )
    for title, tools in groups:
        lines += [f"## {title}", ""]
        for tool in tools:
            lines += _entry(tool)
    lines += [
        "## Forbidden tools",
        "",
        "A forbidden tool is named so that a call of it is denied by its name. No model is"
        " given one, and none is ever carried out.",
        "",
        "| Tool | What it would do |",
        "| --- | --- |",
    ]
    for tool in registry.tools:
        if tool.risk == FORBIDDEN:
            lines.append(f"| `{tool.name}` | {_cell(_line(tool.description))} |")
    return "\n".join(lines) + "\n"
