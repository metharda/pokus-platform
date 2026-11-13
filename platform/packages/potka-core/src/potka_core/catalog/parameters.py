"""A scenario's parameters: how a seed picks their values, and the templates that use them.

A scenario is a template; giving each of its parameters a value makes an
instance. A value is derived from the instance's scenario seed, a signed
64-bit integer, and the parameter's name:

    N = HMAC-SHA256(key = the seed's 8 bytes, big-endian two's complement;
                    message = the name's bytes)

read as an unsigned big-endian integer. The parameter takes candidate
number ``N mod n`` of its n candidates, counted from 0: a choice's values
in the order they are written, a range's integers from ``min`` upward.
2**256 is so much larger than any n that no candidate is more likely than
another by more than n / 2**256. The same seed and name always give the
same value, and the parameters of one instance are independent of each
other.

Strings of a scenario and of its ground truth may use the parameters:
``${name}`` stands for the value of the parameter NAME, ``$$`` for one
dollar sign, and any other ``$`` is refused. A string that is one
placeholder and nothing else becomes the value itself, with its type;
inside other text a value is written as JSON writes it, a string as it is.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping, Sequence
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    Discriminator,
    Field,
    JsonValue,
    Tag,
    model_validator,
)

from potka_core.canonical import SAFE_INTEGER, canonical_json
from potka_core.catalog.base import Count, Json, Model, plain

SEED_MIN, SEED_MAX = -(2**63), 2**63 - 1
type Scalar = str | int | float | bool
Integer = Annotated[int, Field(ge=-SAFE_INTEGER, le=SAFE_INTEGER)]

KINDS = "a parameter's type is choice or range"
_NAME = r"[a-z][a-z0-9_]{0,62}"
_TOKEN = re.compile(rf"\$(?:(?P<dollar>\$)|\{{(?P<name>{_NAME})\}})?")
_WHOLE = re.compile(rf"\$\{{({_NAME})\}}")


class TemplateError(ValueError):
    """A string misuses ``$`` or names a parameter that has no value."""


def _stray(match: re.Match[str]) -> TemplateError:
    # The place only: the text is restricted content.
    return TemplateError(
        f"the '$' at character {match.start() + 1} is neither '$$' nor the start of '${{name}}'"
    )


class Choice(Model):
    """One of the listed values, each as likely as the others."""

    type: Literal["choice"]
    """The kind of parameter."""
    values: Annotated[tuple[Annotated[Scalar, AfterValidator(plain)], ...], Field(min_length=1)]
    """The values to choose from: strings, numbers or booleans, none of them twice.

    Two numbers of one value, such as 1 and 1.0, are one value.
    """

    @model_validator(mode="after")
    def _distinct(self) -> Choice:
        # As canonical JSON, which records an instance's values: true, 1 and "1" differ
        # there, 1 and 1.0 do not.
        if len({canonical_json(value) for value in self.values}) != len(self.values):
            raise ValueError("a choice lists a value twice")
        return self

    def candidates(self) -> Sequence[Scalar]:
        """The values, in the order they are written."""
        return self.values


class Range(Model):
    """An integer from ``min`` to ``max``, ``step`` apart, each as likely as the others."""

    type: Literal["range"]
    """The kind of parameter."""
    min: Integer
    """The smallest value."""
    max: Integer
    """The largest value, a whole number of steps above the smallest."""
    step: Count = 1
    """The distance between two neighbouring values."""

    @model_validator(mode="after")
    def _reaches_max(self) -> Range:
        if self.min > self.max:
            raise ValueError("a range's min is above its max")
        if (self.max - self.min) % self.step:
            raise ValueError("a range's max is not a whole number of steps above its min")
        return self

    def candidates(self) -> Sequence[int]:
        """The integers, from ``min`` upward."""
        return range(self.min, self.max + 1, self.step)


def _kind(value: object) -> str | None:
    kind = value.get("type") if isinstance(value, dict) else getattr(value, "type", None)
    return kind if kind in ("choice", "range") else None


Parameter = Annotated[
    Annotated[Choice, Tag("choice")] | Annotated[Range, Tag("range")],
    # One message for every other type: pydantic's own would show the type given.
    Discriminator(_kind, custom_error_type="parameter_type", custom_error_message=KINDS),
]


def derive(seed: int, name: str, parameter: Choice | Range) -> Scalar:
    """The value of the parameter NAME in the instance of the scenario seed SEED.

    Args:
        seed: The instance's scenario seed, a signed 64-bit integer.
        name: The parameter's name.
        parameter: The parameter.

    Returns:
        The value (the module's docstring defines it).

    Raises:
        ValueError: SEED is not a signed 64-bit integer.
    """
    if type(seed) is not int or not SEED_MIN <= seed <= SEED_MAX:
        raise ValueError("the scenario seed is not a signed 64-bit integer")
    key = seed.to_bytes(8, "big", signed=True)
    number = int.from_bytes(hmac.new(key, name.encode(), hashlib.sha256).digest(), "big")
    candidates = parameter.candidates()
    return candidates[number % len(candidates)]


def derive_all(seed: int, parameters: Mapping[str, Choice | Range]) -> dict[str, Scalar]:
    """The value of every parameter in the instance of the scenario seed SEED.

    Args:
        seed: The instance's scenario seed, a signed 64-bit integer.
        parameters: The scenario's parameters by name.

    Returns:
        Each parameter's value by name.
    """
    return {name: derive(seed, name, parameter) for name, parameter in parameters.items()}


def placeholders(text: str) -> tuple[str, ...]:
    """The parameters TEXT uses.

    Args:
        text: A string of a scenario or a ground truth.

    Returns:
        The names, in the order of their placeholders (a name as often as it is used).

    Raises:
        TemplateError: A ``$`` is neither ``$$`` nor the start of ``${name}``.
            The message gives its place, never the text.
    """
    names = []
    for match in _TOKEN.finditer(text):
        if match["name"] is not None:
            names.append(match["name"])
        elif match["dollar"] is None:
            raise _stray(match)
    return tuple(names)


def tree_placeholders(value: JsonValue) -> tuple[str, ...]:
    """The parameters the strings of VALUE use, at any depth (keys are not templates).

    Args:
        value: JSON data.

    Returns:
        The names (a name as often as it is used).

    Raises:
        TemplateError: A string misuses ``$`` (`placeholders`).
    """
    if isinstance(value, str):
        return placeholders(value)
    if isinstance(value, dict):
        return tuple(name for item in value.values() for name in tree_placeholders(item))
    if isinstance(value, list):
        return tuple(name for item in value for name in tree_placeholders(item))
    return ()


def _value(values: Mapping[str, Scalar], name: str) -> Scalar:
    if name not in values:
        raise TemplateError(f"no parameter {name}")
    return values[name]


def render_text(text: str, values: Mapping[str, Scalar]) -> str:
    """TEXT with every placeholder replaced by its value's text.

    Args:
        text: A string of a scenario or a ground truth.
        values: The instance's parameter values by name.

    Returns:
        The text: a string value as it is, any other as JSON writes it.

    Raises:
        TemplateError: TEXT misuses ``$`` or names a parameter VALUES lacks.
    """

    def fill(match: re.Match[str]) -> str:
        if match["dollar"] is not None:
            return "$"
        if match["name"] is None:
            raise _stray(match)
        value = _value(values, match["name"])
        return value if isinstance(value, str) else json.dumps(value)

    return _TOKEN.sub(fill, text)


def render(value: JsonValue, values: Mapping[str, Scalar]) -> JsonValue:
    """VALUE with the placeholders of its strings filled, at any depth.

    Args:
        value: JSON data (keys are not templates).
        values: The instance's parameter values by name.

    Returns:
        A copy: a string that is one placeholder becomes the parameter's
        value with its type, any other string its text (`render_text`).

    Raises:
        TemplateError: A string misuses ``$`` or names a parameter VALUES lacks.
    """
    if isinstance(value, str):
        whole = _WHOLE.fullmatch(value)
        return _value(values, whole[1]) if whole else render_text(value, values)
    if isinstance(value, dict):
        return {key: render(item, values) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, values) for item in value]
    return value


def _checked(value: JsonValue, where: str) -> None:
    """Refuse a string of VALUE that misuses ``$``, naming its place inside the data."""
    if isinstance(value, str):
        try:
            placeholders(value)
        except TemplateError as exc:
            raise TemplateError(f"{where}: {exc}" if where else str(exc)) from None
    elif isinstance(value, dict):
        for key, item in value.items():
            _checked(item, f"{where}.{key}" if where else key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _checked(item, f"{where}.{index}" if where else str(index))


def _templates(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    _checked(value, "")
    return value


def _template(text: str) -> str:
    placeholders(text)
    return text


Params = Annotated[dict[str, Json], AfterValidator(_templates)]
"""A step's parameters: JSON data whose strings may use the scenario's parameters."""
Template = Annotated[str, Field(min_length=1, pattern=r"\S"), AfterValidator(_template)]
"""Text that may use the scenario's parameters."""
