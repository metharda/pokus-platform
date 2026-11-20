"""The tool registry: the format of its files, their form rules and its loader.

A registry is a directory that holds ``VERSION`` (the registry's version, a
positive integer) and ``*.yaml`` files, each a list of tool entries, and
nothing else. A tool is what a model may call: a name, a description the
model reads, a risk, its arguments, and either a command (``exec``) or one
of the handlers the executing service implements (``handler``), never
both. A tool with the risk ``forbidden`` has neither: it is in the registry
to be named and denied. How a command is written is in `potka_tools.template`.

A mistake in a registry becomes a command in a target, so a registry is
loaded whole or not at all: every form rule is checked for every entry, and
:class:`RegistryError` carries all that was found. The rules a schema can
say are in the models' fields; the others (what the fields of one entry say
about each other) are in their validators.

An argument has one of four types. A ``string`` is printable ASCII and has
a ``pattern``, an ``enum`` or a ``from`` (a source of the run's context,
beside which a pattern may stand); an ``integer`` has both bounds; a
``path`` has exactly one of a ``from`` and a ``within`` (a list of roots it
must lie below); a ``text`` is free text for a handler and is never part of
a command. So no argument reaches a command or a handler without a rule for
its value.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from potka_core.canonical import SAFE_INTEGER, canonical_json
from potka_core.yaml_subset import parse_yaml
from pydantic import AfterValidator, Field, TypeAdapter, ValidationError, model_validator

from potka_tools.template import Marked, Model, Template, placeholders, standalone
from potka_tools.values import below, is_normal, is_text, printable, refused

VERSION: Final = "VERSION"
REGISTRY: Final = Path("tools") / "registry"
DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"
NOTE: Final = (
    "The models that read a registry are stricter than this schema: they convert nothing, "
    "hold a command's elements to their forms and check what the fields of one entry say "
    "about each other. A file that matches the schema may still be refused."
)
STRING_MAX: Final = 1024
PATH_MAX: Final = 4096
TEXT_MAX: Final = 262_144
DESCRIPTION_MAX: Final = 1000
INTEGER_MAX: Final = 2**31 - 1
NODES: Final = "context.nodes"
STRING_SOURCES: Final = (NODES, "context.node.services", "context.node.http_endpoints")
PATH_SOURCES: Final = ("context.node.readable_paths", "context.node.patchable_paths")
FORBIDDEN: Final = "forbidden"
# What an argument of each type may say beside its type, its description and a default.
_MAY: Final[Mapping[str, frozenset[str]]] = {
    "string": frozenset({"pattern", "enum", "source", "max_length"}),
    "integer": frozenset({"minimum", "maximum"}),
    "path": frozenset({"source", "within", "max_length"}),
    "text": frozenset({"max_length"}),
}
_LENGTHS: Final[Mapping[str, int]] = {"string": STRING_MAX, "path": PATH_MAX, "text": TEXT_MAX}
_RULES: Final = ("pattern", "enum", "minimum", "maximum", "max_length", "source", "within")
# How an error says what a handler's argument must have.
_HAS: Final[Mapping[str, str]] = {"pattern": " with a pattern", "enum": " with an enum", "": ""}
# The arguments each handler reads: the type of each, and what it must have (a source of
# the context, a pattern or an enum; nothing for a text).
HANDLERS: Final[Mapping[str, Mapping[str, tuple[str, str]]]] = {
    "list_nodes": {},
    "read_file": {
        "node": ("string", NODES),
        "path": ("path", "context.node.readable_paths"),
    },
    "apply_config_patch": {
        "node": ("string", NODES),
        "path": ("path", "context.node.patchable_paths"),
        "diff": ("text", ""),
    },
    "query_logs": {
        "node": ("string", NODES),
        "service": ("string", "context.node.services"),
        "since": ("string", "pattern"),
        "level": ("string", "enum"),
    },
    "query_metrics": {
        "node": ("string", NODES),
        "metric": ("string", "enum"),
        "since": ("string", "pattern"),
    },
}
# The handlers that change their target: a tool of one is mutating, a tool of any other is
# read-only, whatever its entry says of itself.
CHANGING: Final = frozenset({"apply_config_patch"})

Code = Literal["wrong_type", "not_allowed", "out_of_range", "bad_form"]
"""What is wrong with a value for its argument, by the rules that need no context."""
Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")]
"""The name of a tool or of an argument: lowercase words joined by underscores."""
Label = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")]
"""A taxonomy label: lowercase words joined by dots, the broadest first."""


def _one_line(value: str) -> str:
    if not is_text(value) or "\n" in value or "\t" in value or not value.strip():
        raise ValueError("a description is one line of printing characters")
    return value


Text = Annotated[str, Field(max_length=DESCRIPTION_MAX), AfterValidator(_one_line)]
"""A description: one line of printing characters, not blank, of at most 1,000."""
Count = Annotated[int, Field(ge=1, le=SAFE_INTEGER)]
"""A positive integer canonical JSON can hold."""


class Arg(Model):
    """An argument of a tool: its type, and the rule its value must fit."""

    type: Literal["string", "integer", "path", "text"]
    """What the value is. A text is for a handler only and never part of a command."""
    description: Text | None = None
    """What the argument means; the model reads it."""
    default: str | int | None = None
    """The value of a call that leaves the argument out; without one it is required."""
    pattern: str | None = None
    """A string's form: a regular expression from ``^`` to ``$``, matched whole, ASCII."""
    enum: Annotated[tuple[str, ...], Field(min_length=1)] | None = None
    """The values a string may have."""
    minimum: int | None = None
    """An integer's smallest value."""
    maximum: int | None = None
    """An integer's largest value."""
    max_length: Annotated[int, Field(ge=1)] | None = None
    """The most characters of a string, a path or a text."""
    source: (
        Literal[
            "context.nodes",
            "context.node.services",
            "context.node.http_endpoints",
            "context.node.readable_paths",
            "context.node.patchable_paths",
        ]
        | None
    ) = Field(default=None, alias="from")
    """The list of the run's context that the value must be in."""
    within: Annotated[tuple[str, ...], Field(min_length=1)] | None = None
    """The roots a path must lie below, each an absolute path in its normal form."""

    @property
    def length(self) -> int:
        """The most characters the value may have (not for an integer)."""
        return self.max_length if self.max_length is not None else _LENGTHS[self.type]

    def problem(self, value: object) -> Code | None:
        """What is wrong with VALUE for this argument, by the rules that need no context.

        A path is only looked at as it was given: that it is absolute text
        of the right length. Its normal form and where it lies are checked
        with a context.

        Returns:
            The first of: ``wrong_type``, ``not_allowed`` (not of the enum),
            ``out_of_range``, ``bad_form``; None when nothing is wrong.
        """
        if self.type == "integer":
            # A boolean is no integer here, although Python counts it as one.
            if type(value) is not int:
                return "wrong_type"
            low, high = self.minimum, self.maximum
            fits = low is not None and high is not None and low <= value <= high
            return None if fits else "out_of_range"
        if type(value) is not str:
            return "wrong_type"
        if self.enum is not None and value not in self.enum:
            return "not_allowed"
        if len(value) > self.length:
            return "bad_form"
        if self.type == "text":
            return None if is_text(value) else "bad_form"
        if not printable(value) or (self.type == "path" and not value.startswith("/")):
            return "bad_form"
        if self.pattern is not None and re.fullmatch(self.pattern, value, re.ASCII) is None:
            return "bad_form"
        return None

    @model_validator(mode="after")
    def _form(self) -> Arg:
        said = {name for name in _RULES if getattr(self, name) is not None}
        kind = "an integer" if self.type == "integer" else f"a {self.type}"
        extra = sorted(said - _MAY[self.type])
        if extra:
            raise ValueError(f"{kind} has no {extra[0].replace('source', 'from')}")
        if self.type == "string":
            if not said & {"pattern", "enum", "source"}:
                raise ValueError("a string has a pattern, an enum or a from")
            if self.enum is not None and said & {"pattern", "source"}:
                raise ValueError("an enum stands without a pattern and without a from")
            if self.source is not None and self.source not in STRING_SOURCES:
                raise ValueError(f"a string's from is one of: {', '.join(STRING_SOURCES)}")
        elif self.type == "integer":
            if self.minimum is None or self.maximum is None:
                raise ValueError("an integer has a minimum and a maximum")
            if not -INTEGER_MAX <= self.minimum <= self.maximum <= INTEGER_MAX:
                raise ValueError(f"an integer's bounds are in order and within {INTEGER_MAX}")
        elif self.type == "path":
            if (self.source is None) == (self.within is None):
                raise ValueError("a path has exactly one of from and within")
            if self.source is not None and self.source not in PATH_SOURCES:
                raise ValueError(f"a path's from is one of: {', '.join(PATH_SOURCES)}")
        elif self.max_length is None:
            raise ValueError("a text has a max_length")
        if self.max_length is not None and self.max_length > _LENGTHS[self.type]:
            raise ValueError(f"{kind}'s max_length is at most {_LENGTHS[self.type]}")
        if self.pattern is not None:
            _check_pattern(self.pattern)
        if self.enum is not None:
            if len(set(self.enum)) != len(self.enum):
                raise ValueError("enum gives a value twice")
            if not all(printable(value) and len(value) <= self.length for value in self.enum):
                raise ValueError("an enum's value is printable ASCII of the argument's length")
        if self.within is not None:
            if len(set(self.within)) != len(self.within):
                raise ValueError("within gives a root twice")
            if not all(
                printable(root) and is_normal(root) and not refused(root) for root in self.within
            ):
                raise ValueError(
                    "a root of within is an absolute path of printable ASCII in its normal"
                    " form, not /, and not in /dev, /proc or /sys"
                )
        if self.default is not None:
            self._check_default(self.default)
        return self

    def _check_default(self, default: str | int) -> None:
        if self.source is not None or self.type == "text":
            raise ValueError("an argument with from, and a text, has no default")
        if self.problem(default) is not None:
            raise ValueError("the default does not fit its own argument")
        if self.within is not None and not (
            isinstance(default, str) and is_normal(default) and below(default, self.within)
        ):
            raise ValueError("a path's default is in its normal form and below a root of within")


def _check_pattern(pattern: str) -> None:
    """Refuse a pattern that does not run from ``^`` to ``$`` or does not compile."""
    body = pattern.removesuffix("$")
    # After an odd number of backslashes the last $ is a dollar sign, not the end.
    escaped = (len(body) - len(body.rstrip("\\"))) % 2 == 1
    if not pattern.startswith("^") or body == pattern or escaped:
        raise ValueError("a pattern begins with ^ and ends with $")
    try:
        re.compile(pattern, re.ASCII)
    except re.error:
        raise ValueError("a pattern is a regular expression Python compiles") from None


class Exec(Model):
    """The command of a tool, and its limits."""

    argv: Template
    """The program and its arguments; an element may hold one argument's value."""
    on: Literal["node", "probe_client"] = "node"
    """Where it runs: in the node the call names, or on the run's probe client."""
    timeout_s: Annotated[int, Field(ge=1, le=300)]
    """The seconds after which the command is ended."""
    kill_after_s: Annotated[int, Field(ge=1, le=30)]
    """The seconds after that end's signal at which the command is killed."""
    max_output_bytes: Annotated[int, Field(ge=1, le=1_048_576)]
    """The most bytes of each output stream that the model is given."""


class Check(Model):
    """A command that is run after a tool's own, to show what it did."""

    argv: Template
    """The program and its arguments."""


class Tool(Model):
    """A tool: what a model may call, and what stands behind it."""

    name: Name
    """The tool's name."""
    version: Count
    """The version of this entry; it rises when the entry changes."""
    description: Text
    """What the tool does; the model reads it."""
    risk: Literal["read", "low", "medium", "high", "forbidden"]
    """How much the tool can change: read for none; a forbidden tool is always denied."""
    mutating: bool
    """Whether the tool changes its target."""
    applicable_categories: tuple[Label, ...]
    """The diagnoses for which a mutating tool is a fitting action: taxonomy labels."""
    args: dict[Name, Arg] = Field(default_factory=dict)
    """The tool's arguments by name. A dictionary for the models' sake: nothing changes it."""
    command: Exec | None = Field(default=None, alias="exec")
    """The command the tool runs."""
    handler: (
        Literal["list_nodes", "read_file", "apply_config_patch", "query_logs", "query_metrics"]
        | None
    ) = None
    """The handler of the executing service that carries the tool out, in place of a command."""
    post_check: Check | None = None
    """A command run after the tool's own; only beside exec."""
    max_output_bytes: Annotated[int, Field(ge=1, le=1_048_576)] | None = None
    """The most bytes a handler's tool gives the model; exactly beside handler."""

    @model_validator(mode="after")
    def _form(self) -> Tool:
        if (self.risk == "read") == self.mutating:
            raise ValueError("risk is read exactly when mutating is false")
        if len(set(self.applicable_categories)) != len(self.applicable_categories):
            raise ValueError("applicable_categories gives a label twice")
        behind = (self.command, self.handler, self.post_check, self.max_output_bytes)
        if self.risk == FORBIDDEN:
            if self.args or self.applicable_categories or any(b is not None for b in behind):
                raise ValueError(
                    "a forbidden tool has no args, no category, no exec, handler,"
                    " post_check or max_output_bytes"
                )
            return self
        if bool(self.applicable_categories) != self.mutating:
            raise ValueError(
                "a mutating tool lists at least one category, and a read-only tool none"
            )
        if (self.command is None) == (self.handler is None):
            raise ValueError("a tool has exactly one of exec and handler")
        if self.post_check is not None and self.command is None:
            raise ValueError("post_check stands only beside exec")
        if (self.max_output_bytes is None) != (self.handler is None):
            raise ValueError("the tool's own max_output_bytes stands exactly beside handler")
        if self.handler is not None:
            self._check_handler(self.handler)
            return self
        node = self.args.get("node")
        if node is None or node.source != NODES:
            raise ValueError(f"a tool has the argument node, a string from {NODES}")
        if any(arg.type == "text" for arg in self.args.values()):
            raise ValueError("a text is an argument of a handler's tool only")
        if self.command is not None:
            self._check_template(self.command.argv)
        if self.post_check is not None:
            self._check_template(self.post_check.argv)
        return self

    def _check_template(self, template: tuple[str | Marked, ...]) -> None:
        """Hold a command to the arguments it names; its forms are the template's own."""
        alone = standalone(template)
        for name in placeholders(template):
            arg = self.args.get(name)
            if arg is None or name == "node":
                raise ValueError(
                    f"a placeholder names an argument of the tool, and not node: {name}"
                )
            if name not in alone:
                continue
            # A value that is a whole element of a command must not read as an option.
            dashed = [arg.default, *(arg.enum or ())]
            if any(isinstance(value, str) and value.startswith("-") for value in dashed):
                raise ValueError(f"a value that stands alone does not begin with -: {name}")
            if arg.minimum is not None and arg.minimum < 0:
                raise ValueError(f"an integer that stands alone is not negative: {name}")

    def _check_handler(self, handler: str) -> None:
        reads = HANDLERS[handler]
        if (handler in CHANGING) != self.mutating:
            raise ValueError(
                f"the handler {handler} changes its target: its tool is mutating"
                if handler in CHANGING
                else f"the handler {handler} changes nothing: its tool's risk is read"
            )
        if set(self.args) != set(reads):
            raise ValueError(
                f"the handler {handler} has exactly the arguments: {', '.join(reads) or 'none'}"
            )
        for name, (kind, has) in reads.items():
            arg = self.args[name]
            fits = {
                "pattern": arg.pattern is not None and arg.source is None,
                "enum": arg.enum is not None,
            }.get(has, (arg.source or "") == has)
            if arg.type != kind or not fits:
                raise ValueError(
                    f"the handler {handler} reads {name} as a {kind}{_HAS.get(has, f' from {has}')}"
                )


@dataclass(frozen=True)
class Registry:
    """A registry whose every entry has its form.

    Attributes:
        version: The registry's version.
        tools: Its tools, sorted by name.
        hash: The sha256, in hexadecimal, of the canonical JSON of the
            version and the tools as the models hold them (defaults filled
            in). Comments, the order of files, entries and keys, and a
            default written out or left out do not change it.
    """

    version: int
    tools: tuple[Tool, ...]
    hash: str

    @classmethod
    def of(cls, version: int, tools: tuple[Tool, ...]) -> Registry:
        """The registry of these tools, with its hash."""
        ordered = tuple(sorted(tools, key=lambda tool: tool.name))
        content: dict[str, Any] = {
            "version": version,
            "tools": [tool.model_dump(mode="json", by_alias=True) for tool in ordered],
        }
        return cls(version, ordered, hashlib.sha256(canonical_json(content)).hexdigest())

    def tool(self, name: str) -> Tool | None:
        """The tool of this name, or None."""
        return next((tool for tool in self.tools if tool.name == name), None)


class RegistryError(ValueError):
    """A registry cannot be loaded.

    Attributes:
        problems: Every problem found, each with the file it is about
            (relative to the registry's directory; ``.`` for the directory).
    """

    def __init__(self, problems: list[tuple[str, str]]) -> None:
        """Keep the problems.

        Args:
            problems: Each file and what is wrong there.
        """
        super().__init__("; ".join(f"{path}: {reason}" for path, reason in problems))
        self.problems = tuple(problems)


def format_schema() -> dict[str, Any]:
    """The JSON Schema of a registry file: a list of tool entries."""
    return {"$schema": DIALECT, "$comment": NOTE, **TypeAdapter(list[Tool]).json_schema()}


def _shown(part: object) -> str:
    """A part of a problem's place: an index, or a key when it has the form of a name.

    A key that an entry should not have is the entry's own text: one of another form
    (a control character, a line of its own) is not shown. ``[key]`` is the models' own
    word for a mapping's key.
    """
    if isinstance(part, int):
        return str(part)
    named = isinstance(part, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}|\[key\]", part)
    return str(part) if named else "?"


def _named(entry: Path) -> str:
    """A file's name as a problem shows it; one that is not printable ASCII is not shown."""
    return entry.name if printable(entry.name) else "(a name that is not printable ASCII)"


def _entries(path: Path, problems: list[tuple[str, str]]) -> list[Tool]:
    """The tools of one file, each problem recorded under the file's name."""
    name = _named(path)
    try:
        data = parse_yaml(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        problems.append((name, "not UTF-8"))
        return []
    except OSError as exc:
        problems.append((name, f"cannot be read ({type(exc).__name__})"))
        return []
    except ValueError as exc:
        problems.append((name, str(exc)))
        return []
    if not isinstance(data, list) or not data:
        problems.append((name, "not a list of tool entries"))
        return []
    tools = []
    for index, entry in enumerate(data, 1):
        given = entry.get("name") if isinstance(entry, dict) else None
        called = given if isinstance(given, str) and printable(given) else f"entry {index}"
        try:
            tools.append(Tool.model_validate_json(json.dumps(entry), strict=True))
        except ValidationError as exc:
            for error in exc.errors(include_url=False, include_input=False, include_context=False):
                where = ".".join(_shown(part) for part in error["loc"])
                said = f"{where}: {error['msg']}" if where else error["msg"]
                problems.append((name, f"{called}: {said}"))
    return tools


def load_registry(directory: Path | None = None) -> Registry:
    """Read the registry in DIRECTORY.

    Args:
        directory: The directory with ``VERSION`` and the ``*.yaml`` files.
            None, as every service calls it, is ``tools/registry`` in the
            release: the directory beside the running environment
            (``.venv``).

    Returns:
        The registry.

    Raises:
        RegistryError: The directory is none or holds something else, the
            version is no positive integer, a file is not YAML of the
            platform's subset or no list of entries, an entry breaks a form
            rule, or a name occurs twice. It carries every problem.
    """
    problems: list[tuple[str, str]] = []
    if directory is None:
        directory = Path(sys.prefix).parent / REGISTRY
    try:
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except OSError as exc:
        raise RegistryError([(".", f"cannot be read ({type(exc).__name__})")]) from None
    version = 0
    found: dict[str, tuple[str, Tool]] = {}
    for entry in entries:
        if entry.is_symlink() or not entry.is_file():
            problems.append((_named(entry), "not a file"))
        elif entry.name == VERSION:
            try:
                written = entry.read_bytes()
            except OSError as exc:
                problems.append((VERSION, f"cannot be read ({type(exc).__name__})"))
                continue
            # Bytes, so that no other line end than the line feed passes for one.
            if re.fullmatch(rb"[1-9][0-9]{0,8}\n?", written) is None:
                problems.append((VERSION, "not one positive integer"))
            else:
                version = int(written)
        elif entry.name.endswith(".yaml"):
            for tool in _entries(entry, problems):
                if tool.name in found:
                    problems.append((_named(entry), f"{tool.name}: also in {found[tool.name][0]}"))
                else:
                    found[tool.name] = (_named(entry), tool)
        else:
            problems.append((_named(entry), f"neither {VERSION} nor a .yaml file"))
    if not any(entry.name == VERSION for entry in entries):
        problems.append((VERSION, "missing"))
    if problems:
        raise RegistryError(problems)
    return Registry.of(version, tuple(tool for _, tool in found.values()))
