"""Checking one call: a tool's arguments, as a model gave them, against a run's context.

Nothing a model writes reaches a command or a handler unchecked. A call's
arguments are checked in a fixed order, and the first check that fails is
the error; within a check, the first argument in name order:

1. the arguments are a JSON object;
2. the tool's JSON Schema, the one the model was given (`argument_schema`),
   and the rules of each argument that need no context (`Arg.problem`): an
   unknown argument, else a missing one, else the first argument with a
   value that is of the wrong type, not allowed, out of range or of a bad
   form. The schema is what the model was told; the rules are what binds:
   a pattern matches the whole value, an integer is no boolean and no
   float, a string and a path are printable ASCII;
3. the node is one of the run's;
4. a value with a source is in that list of the node;
5. a path, made normal, is allowed;
6. a value that is a whole element of the tool's command, or of the command
   that checks it afterwards, is not empty and does not begin with ``-``.
   This is a floor under every tool, so that no value reads as an option;
   what a value may be to its program is its argument's pattern, enum or
   source.

A call is checked whole before anything is decided about it: what could
keep its command from being made is found here, and making the command
cannot fail afterwards.

A call that passes becomes a :class:`Call`: the arguments in their checked
form (defaults filled in, paths normal) and what each of them is in a
command. An :class:`ArgumentError` names the argument and the rule and
never a value: not the one given, and nothing of the context, so an error
tells a model nothing that a successful call would not.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Literal, cast

import jsonschema

from potka_tools.context import Context, NodeContext
from potka_tools.registry import FORBIDDEN, NODES, Arg, Tool
from potka_tools.template import standalone
from potka_tools.values import below, is_text, normalize, refused, within

ErrorCode = Literal[
    "not_an_object",
    "forbidden",
    "unknown_argument",
    "missing_argument",
    "wrong_type",
    "not_allowed",
    "out_of_range",
    "bad_form",
    "unknown_node",
    "not_in_context",
    "path_not_allowed",
    "not_renderable",
    "bad_meta",
]
# Of one argument's problems the first of these is the one reported.
_ORDER: Final[tuple[ErrorCode, ...]] = ("wrong_type", "not_allowed", "out_of_range", "bad_form")
# What the schema's keywords say, in the words of the codes.
_KEYWORDS: Final[Mapping[str, ErrorCode]] = {
    "type": "wrong_type",
    "enum": "not_allowed",
    "minimum": "out_of_range",
    "maximum": "out_of_range",
    "pattern": "bad_form",
    "minLength": "bad_form",
    "maxLength": "bad_form",
}
_SAID: Final[Mapping[ErrorCode, str]] = {
    "wrong_type": "not of the type the tool asks for",
    "not_allowed": "not one of its allowed values",
    "out_of_range": "not within its bounds",
    "bad_form": "not of the form the tool asks for",
}
META: Final = "_meta"
META_TEXT_MAX: Final = 2000
META_SCHEMA: Final[Mapping[str, Any]] = {
    "type": "object",
    "properties": {
        "action_confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "How confident you are that this action is right, from 0 to 1.",
        },
        "rationale": {
            "type": "string",
            "minLength": 1,
            "maxLength": META_TEXT_MAX,
            "description": "Why you take this action.",
        },
        "expected_effect": {
            "type": "string",
            "minLength": 1,
            "maxLength": META_TEXT_MAX,
            "description": "What you expect the action to change.",
        },
    },
    "required": ["action_confidence", "rationale", "expected_effect"],
    "additionalProperties": False,
}
"""What a mutating call says of itself; the model writes it among the call's arguments."""


class ArgumentError(ValueError):
    """A call's arguments do not fit its tool or the run's context.

    Attributes:
        code: Which rule was broken; one of a closed list.
        argument: The argument's name, or None when the error is of none
            or of an argument the tool has not, whose name is not lowercase
            words (an unknown name of that form is given back, ``_meta``
            too, so that a model sees which of its arguments was refused).
    """

    def __init__(self, code: ErrorCode, argument: str | None, message: str) -> None:
        """Keep the code and the argument.

        Args:
            code: Which rule was broken.
            argument: The argument's name, or None.
            message: The rule in words; no value of the call or the context.
        """
        super().__init__(message if argument is None else f"{argument}: {message}")
        self.code = code
        self.argument = argument


@dataclass(frozen=True)
class Call:
    """A call whose arguments were checked.

    Attributes:
        tool: The tool.
        node: The alias of the node the call names, or None for a tool
            without one.
        args: The arguments in their checked form: defaults filled in,
            paths normal. Two calls are the same call when these are equal.
        values: What each argument is in a command: a string as it is, an
            integer in decimal digits, a path in its normal form, the name
            of an endpoint as its URL. The node and a text have none.
    """

    tool: Tool
    node: str | None
    args: Mapping[str, str | int]
    values: Mapping[str, str]


@dataclass(frozen=True)
class Meta:
    """What a mutating call says of itself.

    Attributes:
        action_confidence: The model's confidence in the action, 0 to 1.
        rationale: Why the action is taken.
        expected_effect: What the action is expected to change.
    """

    action_confidence: float
    rationale: str
    expected_effect: str


def _property(arg: Arg) -> dict[str, Any]:
    said: dict[str, Any]
    if arg.type == "integer":
        said = {"type": "integer", "minimum": arg.minimum, "maximum": arg.maximum}
    else:
        said = {"type": "string", "minLength": 1, "maxLength": arg.length}
        if arg.pattern is not None:
            said["pattern"] = arg.pattern
        if arg.enum is not None:
            said["enum"] = list(arg.enum)
    if arg.default is not None:
        said["default"] = arg.default
    if arg.description is not None:
        said["description"] = arg.description
    return said


def argument_schema(tool: Tool) -> dict[str, Any]:
    """The JSON Schema of TOOL's arguments, as a model is given it.

    It says a value's type, bounds, length, pattern, enum and default. It
    says nothing of where a value must come from in a run: no source, no
    root of a path.
    """
    return {
        "type": "object",
        "properties": {name: _property(arg) for name, arg in sorted(tool.args.items())},
        "required": sorted(name for name, arg in tool.args.items() if arg.default is None),
        "additionalProperties": False,
    }


def _static(tool: Tool, args: Mapping[str, object]) -> None:
    """Step 2: the schema and the rules without a context; raise for the first problem."""
    unknown = sorted(name for name in args if name not in tool.args)
    if unknown:
        # The model's own text: given back only in the form of a name, with or without a
        # leading underscore.
        named = unknown[0] if re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", unknown[0]) else None
        raise ArgumentError("unknown_argument", named, "the tool has no such argument")
    missing = sorted(n for n, arg in tool.args.items() if arg.default is None and n not in args)
    if missing:
        raise ArgumentError("missing_argument", missing[0], "required")
    found: dict[str, set[ErrorCode]] = {}
    validator = jsonschema.Draft202012Validator(argument_schema(tool))
    for error in validator.iter_errors(args):
        if error.absolute_path and str(error.validator) in _KEYWORDS:
            found.setdefault(str(error.absolute_path[0]), set()).add(
                _KEYWORDS[str(error.validator)]
            )
    for name, value in args.items():
        problem = tool.args[name].problem(value)
        if problem is not None:
            found.setdefault(name, set()).add(problem)
    for name in sorted(found):
        code = next(code for code in _ORDER if code in found[name])
        raise ArgumentError(code, name, _SAID[code])


def _in_context(
    tool: Tool, args: Mapping[str, str | int], node: NodeContext | None, context: Context
) -> None:
    """Step 4: every value with a source is in that list: the run's nodes, or the node's."""
    for name, arg in sorted(tool.args.items()):
        value = args[name]
        if name == "node" or arg.source is None or arg.type != "string":
            continue
        if arg.source == NODES:
            listed = context.node(str(value)) is not None
        elif arg.source == "context.node.services":
            listed = node is not None and value in node.services
        else:
            listed = node is not None and node.url(str(value)) is not None
        if not listed:
            raise ArgumentError("not_in_context", name, "not one the run has for it")


def _paths(tool: Tool, args: dict[str, str | int], node: NodeContext | None) -> None:
    """Step 5: every path is made normal and is allowed; ARGS then holds the normal forms."""
    for name, arg in sorted(tool.args.items()):
        if arg.type != "path":
            continue
        normal = normalize(str(args[name]))
        if normal is None:
            raise ArgumentError("bad_form", name, _SAID["bad_form"])
        if arg.within is not None:
            allowed = below(normal, arg.within)
        elif node is None:
            allowed = False
        elif arg.source == "context.node.patchable_paths":
            allowed = within(normal, node.patchable)
        else:
            allowed = within(normal, node.readable)
        # One refusal for both, so that an error does not say which it was.
        if refused(normal) or not allowed:
            raise ArgumentError("path_not_allowed", name, "not a path this tool may be given")
        args[name] = normal


def _values(tool: Tool, args: Mapping[str, str | int], node: NodeContext | None) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, arg in tool.args.items():
        value = args[name]
        if name == "node" or arg.type == "text":
            continue
        if arg.source == "context.node.http_endpoints" and node is not None:
            values[name] = node.url(str(value)) or ""
        else:
            values[name] = str(value)
    return values


def _alone(tool: Tool, values: Mapping[str, str]) -> None:
    """Step 6: no value that is a whole element of a command reads as an option."""
    templates = [said.argv for said in (tool.command, tool.post_check) if said is not None]
    for name in sorted(frozenset[str]().union(*(standalone(template) for template in templates))):
        if not values[name] or values[name].startswith("-"):
            raise ArgumentError("not_renderable", name, "may not be empty or begin with a dash")


def check(tool: Tool, args: object, context: Context) -> Call:
    """Check a call's arguments against its tool and the run's context.

    Args:
        tool: The tool the call names.
        args: The call's arguments as they were given, without ``_meta``.
        context: The run's nodes.

    Returns:
        The checked call.

    Raises:
        ArgumentError: The tool is forbidden, or the arguments do not fit
            (the module's docstring has the order of the checks).
    """
    if tool.risk == FORBIDDEN:
        raise ArgumentError("forbidden", None, f"the tool {tool.name} is forbidden")
    if not isinstance(args, dict) or not all(isinstance(name, str) for name in args):
        raise ArgumentError("not_an_object", None, "the arguments are not an object")
    _static(tool, args)
    # After step 2 a given value is of its argument's type, and one left out has a default,
    # which was held to its argument's own rules when the registry was loaded. The steps
    # after this one read a default like a given value.
    checked = {
        name: cast("str | int", args.get(name, arg.default))
        for name, arg in sorted(tool.args.items())
    }
    node = None
    if "node" in tool.args:
        node = context.node(str(checked["node"]))
        if node is None:
            raise ArgumentError("unknown_node", "node", "not a node of this run")
    _in_context(tool, checked, node, context)
    _paths(tool, checked, node)
    values = _values(tool, checked, node)
    _alone(tool, values)
    # Read-only views: a holder of the call cannot change what was checked.
    return Call(
        tool,
        node.alias if node is not None else None,
        MappingProxyType(checked),
        MappingProxyType(values),
    )


def check_meta(tool: Tool, meta: object) -> Meta | None:
    """Check what a mutating call says of itself.

    Args:
        tool: The tool the call names.
        meta: The call's ``_meta``, or None when it has none.

    Returns:
        The checked statement; None for a read-only tool, whatever was
        given: a model that adds one to a read-only call loses nothing.

    Raises:
        ArgumentError: The tool is forbidden; or it is mutating and META is
            not an object with exactly a confidence from 0 to 1 (a number,
            no boolean), a rationale and an expected effect, each a text
            of at most :data:`META_TEXT_MAX` characters.
    """
    if tool.risk == FORBIDDEN:
        raise ArgumentError("forbidden", None, f"the tool {tool.name} is forbidden")
    if not tool.mutating:
        return None
    wrong = ArgumentError(
        "bad_meta",
        META,
        "an object with action_confidence (0 to 1), rationale and expected_effect is needed",
    )
    if not isinstance(meta, dict) or set(meta) != set(META_SCHEMA["required"]):
        raise wrong
    confidence = meta["action_confidence"]
    # The bounds first: an integer too large for a float is out of them, and is never
    # made a float.
    if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
        raise wrong
    texts = (meta["rationale"], meta["expected_effect"])
    if not math.isfinite(confidence) or not all(
        isinstance(text, str) and len(text) <= META_TEXT_MAX and is_text(text) for text in texts
    ):
        raise wrong
    return Meta(float(confidence), texts[0], texts[1])
