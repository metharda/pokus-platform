"""A tool's command as a registry file writes it: a template, element by element.

A template is a list, and each element of it becomes exactly one element of
the command: nothing is split, joined, dropped or added. An element has one
of four forms:

* a literal: text without ``{`` or ``}`` (``systemctl``, ``--no-pager``);
* a placeholder alone: ``{unit}``, which becomes the argument's value;
* an option: a literal that begins with ``-`` and ends with one placeholder
  (``--lines={lines}``), which becomes the literal and the value together;
* a marked literal: a mapping with the one key ``literal``, whose text is
  taken as it is, braces and all (``{literal: "--format={{json .State}}"}``).

The first element, the program, is a literal. No literal is the name of a
shell: a command is a program and its arguments, never a line for a shell
to read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Final

from potka_core.shells import is_shell
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from potka_tools.values import printable

NAME: Final = r"[a-z][a-z0-9_]{0,62}"
_FILLED_RE: Final = re.compile(rf"([^{{}}]*)\{{({NAME})\}}")
FORMS: Final = (
    "an element is a literal, a placeholder alone or an option that ends with one"
    " placeholder; a literal with braces is marked as {literal: ...}; a placeholder is quoted"
)


class Model(BaseModel):
    """The base of the registry's models: unknown keys refused, nothing converted, frozen."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        # The string below a field is its description in the exported JSON Schema.
        use_attribute_docstrings=True,
    )

    @model_validator(mode="after")
    def _no_null(self) -> Model:
        # A key written without a value would read as a key left out: nothing is guessed.
        for name in sorted(self.model_fields_set):
            if getattr(self, name) is None:
                written = type(self).model_fields[name].alias or name
                raise ValueError(f"{written} has no value: a key is given a value or left out")
        return self


class Marked(Model):
    """An element of a command that is taken as it is although it holds braces."""

    literal: Annotated[str, Field(min_length=1)]
    """The element's text."""


def _quoted(written: object) -> object:
    """Refuse a mapping that is no marked literal with the forms, before the models do.

    YAML reads an unquoted ``{unit}`` as a mapping: the refusal says to quote it.
    """
    if not isinstance(written, list | tuple):
        return written
    for one in written:
        if isinstance(one, dict) and set(one) != {"literal"}:
            raise ValueError(FORMS)
    # What comes back is read as Python's data, which takes no list where a tuple is asked.
    return tuple(written)


Template = Annotated[tuple[str | Marked, ...], BeforeValidator(_quoted), Field(min_length=1)]
"""A command: a program and its arguments, element by element."""


@dataclass(frozen=True)
class Element:
    """One element of a template.

    Attributes:
        literal: Its fixed text: the whole element, or what stands before
            an argument's value (empty for a placeholder alone).
        argument: The argument whose value ends the element, or None for a
            literal.
    """

    literal: str
    argument: str | None

    @property
    def alone(self) -> bool:
        """Whether the element is a placeholder alone: the value is the whole element."""
        return self.argument is not None and not self.literal


def element(written: str | Marked) -> Element:
    """The element a template writes as WRITTEN.

    Raises:
        ValueError: It has none of the four forms, or its fixed text is
            empty or not printable ASCII.
    """
    if isinstance(written, Marked):
        found = Element(written.literal, None)
    elif "{" not in written and "}" not in written:
        found = Element(written, None)
    else:
        filled = _FILLED_RE.fullmatch(written)
        if filled is None or (filled[1] and not filled[1].startswith("-")):
            raise ValueError(FORMS)
        found = Element(filled[1], filled[2])
    if (found.literal or found.argument is None) and not printable(found.literal):
        raise ValueError("an element's text is printable ASCII and not empty")
    return found


def elements(template: tuple[str | Marked, ...]) -> tuple[Element, ...]:
    """The elements of TEMPLATE.

    Raises:
        ValueError: An element has none of the four forms; the first is no
            plain literal; or a literal is the name of a shell.
    """
    found = tuple(element(written) for written in template)
    if not isinstance(template[0], str) or found[0].argument is not None:
        raise ValueError("the first element of a command, its program, is a literal")
    for one in found:
        if one.argument is None and is_shell(one.literal):
            raise ValueError("a command is a program and its arguments, never a shell's")
    return found


def placeholders(template: tuple[str | Marked, ...]) -> tuple[str, ...]:
    """The arguments TEMPLATE names, in its order, each as often as named."""
    return tuple(one.argument for one in elements(template) if one.argument is not None)


def standalone(template: tuple[str | Marked, ...]) -> frozenset[str]:
    """The arguments whose value is a whole element of TEMPLATE."""
    return frozenset(one.argument for one in elements(template) if one.argument and one.alone)
