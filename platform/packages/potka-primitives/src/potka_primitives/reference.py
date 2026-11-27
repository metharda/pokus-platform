"""The reference page of the library: every fault primitive and every probe, as Markdown.

The page is written from the classes themselves, so that it cannot say
anything else than they do: a step's own words, its version, the programs
it runs in a node, the service it acts on, the facts it reports, and its
parameters as their model has them. Of a parameter it gives the words
that stand beside the field, what the model's schema says of a value, and
what each validator of the field asks, in the words that stand at the
validator; what a validator of the whole model asks stands below the
table.

What the page cannot show it does not pass over. A step, a field or a
validator without words, a key of a schema or a mark of a type that it
does not know, and a value that cannot stand in a table end the
generation with an error that says which.
"""

from __future__ import annotations

import inspect
import json
import re
import types
from collections.abc import Mapping
from typing import Annotated, Any, Final, NamedTuple, Union, get_args, get_origin, get_type_hints

from pydantic import AfterValidator, BaseModel
from pydantic.fields import FieldInfo

from potka_primitives import library
from potka_primitives.base import Step

FRONT_MATTER: Final = (
    "---",
    "title: Fault primitives and probes",
    "type: reference",
    "audience: [researcher, scenario-author, developer, reader]",
    'applies_to: {platform: ">=0.0"}',
    "last_verified: {date: null, rc: null, by: null}",
    "---",
)
_HEADER: Final = (
    "| Parameter | Type | Required | Rule | Description |",
    "| --- | --- | --- | --- | --- |",
)
_TYPES: Final = {"array": "list", "object": "mapping"}
# What of a field's schema the page shows or reads; any other key is a rule it would pass over.
_KNOWN: Final = frozenset(
    {
        "anyOf",
        "const",
        "default",
        "description",
        "enum",
        "items",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minimum",
        "pattern",
        "title",
        "type",
    }
)
# What the schema of a list's entries may hold: models that one key tells apart.
_ENTRIES: Final = frozenset({"discriminator", "oneOf"})
# The validators of a model that the page has no place for.
_OTHER_VALIDATORS: Final = ("validators", "field_validators", "root_validators")


class Row(NamedTuple):
    """What the page says of one field of a model."""

    name: str
    kind: str
    needed: str
    rule: str
    words: str


def _prose(text: str) -> str:
    """TEXT outside a code span, with what Markdown would read as markup made plain."""
    for sign in ("\\", "`", "*", "_"):
        text = text.replace(sign, "\\" + sign)
    return text.replace("<", "&lt;")


def _code(value: object) -> str:
    """VALUE as a code span, as it is: text as text, anything else as JSON writes it.

    The span's fence is longer than any run of backticks in the value.

    Raises:
        ValueError: The value holds a line's end, which no span in a table can.
    """
    said = value if isinstance(value, str) else json.dumps(value)
    if "\n" in said or "\r" in said:
        raise ValueError("a value with a line's end cannot stand in a table")
    if "`" not in said:
        return f"`{said}`"
    fence = "`" * (max(len(run) for run in re.findall("`+", said)) + 1)
    return f"{fence} {said} {fence}"


def _words(text: str | None) -> str:
    """TEXT on one line, each run of white space as one space.

    What stands between doubled backticks is code and is kept as it is;
    everything else is prose, in which nothing is markup.

    Raises:
        ValueError: A code span does not end.
    """
    parts = " ".join((text or "").split()).split("``")
    if len(parts) % 2 == 0:
        raise ValueError("words with a code span that does not end")
    return "".join(_code(part) if index % 2 else _prose(part) for index, part in enumerate(parts))


def _cell(text: str) -> str:
    """TEXT as a table's cell holds it: a bar is escaped, in a code span too."""
    return text.replace("|", "\\|")


def _fragment(sentence: str) -> str:
    """A sentence as one part of a rule: without its full stop, its first word in lower case.

    A first word that is a name or code keeps its letters.
    """
    said = sentence.removesuffix(".")
    first = said.split(" ", 1)[0]
    return said[0].lower() + said[1:] if first.isalpha() and first.istitle() else said


def _alternatives(schema: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The forms a value may have, without the form of no value."""
    return [one for one in schema.get("anyOf", [schema]) if one.get("type") != "null"]


def _choices(hint: Any) -> list[Any]:
    """The types a value may have, without the type of no value."""
    if get_origin(hint) in (Union, types.UnionType):
        return [one for one in get_args(hint) if one is not type(None)]
    return [hint]


def _asked(hint: Any, whose: str) -> list[str]:
    """What the validators among a type's marks ask, each in the words that stand at it.

    A validator's first sentence is its rule; what it says further stands
    in brackets after it.

    Raises:
        ValueError: A mark that is neither a field's bounds nor such a
            validator, or a validator without words.
    """
    said = []
    for mark in getattr(hint, "__metadata__", ()) if get_origin(hint) is Annotated else ():
        if isinstance(mark, FieldInfo):
            continue
        if not isinstance(mark, AfterValidator):
            raise ValueError(f"{whose}: a mark the page cannot show ({type(mark).__name__})")
        words = inspect.getdoc(mark.func)
        if not words:
            raise ValueError(f"{whose}: a validator without words ({mark.func.__name__})")
        first, *more = [_fragment(_words(part)) for part in words.split("\n\n")]
        said.append(first + (f" ({'; '.join(more)})" if more else ""))
    return said


def _rule(schema: Mapping[str, Any], whose: str) -> list[str]:
    """What the schema says a value of one form must be, in words.

    Raises:
        ValueError: The form has no type, or holds a key the page does not show.
    """
    unknown = sorted(set(schema) - _KNOWN)
    if unknown or ("type" not in schema and "const" not in schema):
        raise ValueError(
            f"{whose}: a form the page cannot show ({', '.join(unknown) or 'no type'})"
        )
    said = []
    if "const" in schema:
        said.append(_code(schema["const"]))
    if "enum" in schema:
        said.append("one of " + ", ".join(_code(value) for value in schema["enum"]))
    if "minimum" in schema or "maximum" in schema:
        said.append(f"from {schema.get('minimum', 'any')} to {schema.get('maximum', 'any')}")
    if "pattern" in schema:
        said.append(f"matches {_code(schema['pattern'])}")
    if "maxLength" in schema:
        said.append(f"at most {schema['maxLength']} characters")
    if "minItems" in schema or "maxItems" in schema:
        said.append(f"{schema.get('minItems', 0)} to {schema.get('maxItems', 'any')} entries")
    return said


def _rows(model: type[BaseModel], whose: str) -> list[Row]:
    """A row for each field of a model, in the model's order.

    Raises:
        ValueError: A field without words, or one the page cannot show.
    """
    schema = model.model_json_schema()
    hints = get_type_hints(model, include_extras=True)
    required = set(schema.get("required", ()))
    rows = []
    for name, field in schema["properties"].items():
        named = f"{whose}: {name}"
        unknown = sorted(set(field) - _KNOWN)
        if unknown:
            raise ValueError(f"{named}: a form the page cannot show ({', '.join(unknown)})")
        forms, choices = _alternatives(field), _choices(hints[name])
        if len(forms) != len(choices):
            raise ValueError(f"{named}: its schema and its type do not have the same forms")
        kinds = dict.fromkeys(
            _TYPES.get(str(form.get("type")), str(form.get("type"))) for form in forms
        )
        rules = [
            "; ".join([*_rule(form, named), *_asked(choice, named)])
            for form, choice in zip(forms, choices, strict=True)
        ]
        needed = "yes" if name in required else "no"
        if name not in required and field.get("default") is not None:
            needed = f"no; default {_code(field['default'])}"
        words = _words(field.get("description"))
        if not words and "const" not in field:
            raise ValueError(f"{named}: a field without words")
        rows.append(Row(name, " or ".join(kinds), needed, "; or ".join(filter(None, rules)), words))
    return rows


def _together(model: type[BaseModel], whose: str) -> list[str]:
    """What the validators of a whole model ask, each in the words that stand at it.

    Raises:
        ValueError: A validator without words, or one of a kind the page
            has no place for.
    """
    decorators = model.__pydantic_decorators__
    if any(getattr(decorators, kind) for kind in _OTHER_VALIDATORS):
        raise ValueError(f"{whose}: a validator the page cannot show")
    said = []
    for name, validator in decorators.model_validators.items():
        words = inspect.getdoc(validator.func)
        if not words:
            raise ValueError(f"{whose}: a validator without words ({name})")
        said.append(_words(words))
    return said


def _entries(model: type[BaseModel], row: Row, whose: str) -> list[str]:
    """The lines that say what an entry of a list parameter is, when its entries are models.

    Raises:
        ValueError: Entries that the page cannot show: validated values
            that are no models, models that no key tells apart, or a
            model without words or with a validator of its own.
    """
    hint = get_type_hints(model, include_extras=True)[row.name]
    field = model.model_json_schema()["properties"][row.name]
    items = field.get("items")
    base = hint.__origin__ if get_origin(hint) is Annotated else hint
    if items is None or get_origin(base) not in (tuple, list):
        # A list that is one of several forms, or of a type the page does not know.
        if items is not None or any("items" in form for form in _alternatives(field)):
            raise ValueError(f"{whose}: a list the page cannot show")
        return []
    entry = get_args(base)[0]
    marks = getattr(entry, "__metadata__", ()) if get_origin(entry) is Annotated else ()
    keys = [mark.discriminator for mark in marks if isinstance(mark, FieldInfo)]
    models = _choices(entry.__origin__ if get_origin(entry) is Annotated else entry)
    told_apart = (
        len(keys) == 1
        and isinstance(keys[0], str)
        and set(items) == _ENTRIES
        and all(isinstance(one, type) and issubclass(one, BaseModel) for one in models)
    )
    if not told_apart:
        raise ValueError(f"{whose}: entries the page cannot show")
    key = keys[0]
    lines = ["", f"An entry of `{row.name}` is a mapping; its `{key}` says which kind it is:", ""]
    for one in models:
        named = f"{whose}: {one.__name__}"
        words = _words(inspect.cleandoc(one.__dict__.get("__doc__") or ""))
        rows = _rows(one, named)
        kind = [member.rule for member in rows if member.name == key]
        if not words or len(kind) != 1 or _together(one, named):
            raise ValueError(f"{named}: an entry the page cannot show")
        lines.append(f"- `{key}`: {kind[0]}. {words}")
        for member in rows:
            if member.name == key:
                continue
            if _entries(one, member, f"{named}: {member.name}") or member.needed != "yes":
                raise ValueError(f"{named}: {member.name}: a field the page cannot show")
            bounds = "; ".join(filter(None, (member.kind, member.rule)))
            lines.append(f"  - `{member.name}` ({bounds}): {member.words}")
    return lines


def _parameters(step: type[Step[Any]]) -> list[str]:
    rows = _rows(step.Params, step.name)
    lines = list(_HEADER)
    below: list[str] = []
    for row in rows:
        cells = (f"`{row.name}`", row.kind, row.needed, row.rule, row.words)
        lines.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
        below += _entries(step.Params, row, f"{step.name}: {row.name}")
    together = _together(step.Params, step.name)
    if together:
        below += ["", " ".join(together)]
    return [*lines, *below, ""]


def entry(step: type[Step[Any]]) -> list[str]:
    """The lines of one step's section: its words, what it runs and reports, its parameters.

    Raises:
        ValueError: The step has no words of its own that begin with its
            name, or something of it cannot be shown.
    """
    said = inspect.cleandoc(step.__dict__.get("__doc__") or "")
    opening = f"``{step.name}``: "
    if not said.startswith(opening) or said == opening:
        raise ValueError(f"{step.__name__}: no words of its own that begin with its name")
    summary, _, more = said.removeprefix(opening).partition("\n\n")
    summary = _words(summary)
    lines = [f"### `{step.name}`", "", summary[:1].upper() + summary[1:], ""]
    for paragraph in filter(None, more.split("\n\n")):
        lines += [_words(paragraph), ""]
    lines.append(f"- Version: {step.version}.")
    programs = ", ".join(f"`{one.name}` ({one.package})" for one in step.requires.programs)
    lines.append(
        f"- Runs in the node: {programs}." if programs else "- Runs no program in the node."
    )
    if step.requires.service is not None:
        lines.append(
            f"- Acts on the service of the package `{step.requires.service}`, which the golden"
            " image of its node must carry."
        )
    if step.reports:
        lines.append("- Reports: " + ", ".join(_code(name) for name in sorted(step.reports)) + ".")
    return [*lines, "", *_parameters(step)]


def documentation() -> str:
    """The page that lists the library's fault primitives and probes.

    Returns:
        Markdown: every fault primitive and then every probe, by name, each
        with what it does, what it runs and reports, and its parameters.

    Raises:
        ValueError: Something of a step cannot be shown; the error names it.
    """
    lines = [
        *FRONT_MATTER,
        "",
        "<!-- Written by `make primitives` from the library. Do not edit it by hand. -->",
        "",
        "# Fault primitives and probes",
        "",
        f"The library has {len(library.PRIMITIVES)} fault primitives and {len(library.PROBES)}"
        " probes. A scenario's steps name one and give its parameters: a fault primitive in"
        " `setup` and `inject`, a probe in `preconditions`, `confirm`, a ground truth's"
        " `verification` and `collateral`, and a blueprint's `health`. Every step acts on one"
        " node of the scenario's blueprint, named by `node`.",
        "",
        "`potka catalog lint` holds each step's parameters to the step's model, in every"
        " instance of the scenario. A table below has a row for each parameter: its type, what"
        " must hold of its value (Rule) and what it is (Description). What must hold of several"
        " parameters together stands below the table. The lint's finding names the parameter"
        " and says what is wrong with it, and shows no value.",
        "",
        "A fault primitive refuses a target it cannot be made on before it changes anything"
        " there. A probe changes nothing in a target: it passes, fails, or reports an error"
        " when it could not measure. A step runs only the programs that its section lists,"
        " each as a list of arguments and never through a shell.",
        "",
    ]
    for title, steps in (("Fault primitives", library.PRIMITIVES), ("Probes", library.PROBES)):
        lines += [f"## {title}", ""]
        for name in sorted(steps):
            lines += entry(steps[name])
    return "\n".join(lines).rstrip("\n") + "\n"
