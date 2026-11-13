"""What the catalog's models share: strict models, the forms of names, plain JSON values."""

from __future__ import annotations

import math
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue

from potka_core.canonical import SAFE_INTEGER


class Model(BaseModel):
    """The base of every catalog model.

    Unknown keys are refused, values are not converted from other types and
    no field can be reassigned (a dictionary a field holds is still a
    dictionary). The catalog is restricted content, so the text of a
    validation error (``str`` and ``repr``) does not print its input; its
    ``errors()`` and ``json()`` still carry it unless asked not to
    (``include_input=False``). An error's place names keys of the file, and
    the models' own messages name at most a parameter, a tag, a label, or a
    name or a path a file declares; never another value.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        hide_input_in_errors=True,
        # The string below a field is its description in the exported JSON Schema.
        use_attribute_docstrings=True,
    )


ScenarioId = Annotated[str, Field(pattern=r"^(FIXTURE-)?[A-Z][A-Z0-9]*-[0-9]{3}$")]
"""A scenario's ID, the same across its versions: ``NAME-001`` or ``FIXTURE-NAME-001``."""
Label = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")]
"""A taxonomy label: lowercase words joined by dots, the broadest first."""
Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$", max_length=63)]
"""The name of a blueprint or a tag: lowercase words joined by hyphens."""
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=63)]
"""The name of a parameter, a probe or a tool: lowercase words joined by underscores."""
Text = Annotated[str, Field(min_length=1, pattern=r"\S")]
"""Text that is not empty or blank."""
Count = Annotated[int, Field(ge=1, le=SAFE_INTEGER)]
"""A positive integer canonical JSON can hold: a version, a number of seconds, a budget."""


def plain(value: JsonValue) -> JsonValue:
    """VALUE, if canonical JSON can hold every number in it.

    Args:
        value: JSON data.

    Returns:
        VALUE unchanged.

    Raises:
        ValueError: A number at any depth is NaN, an infinity, or an integer
            beyond 2**53 - 1. The message does not show it.
    """
    if isinstance(value, dict):
        for item in value.values():
            plain(item)
    elif isinstance(value, list):
        for item in value:
            plain(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("NaN or an infinity is not a catalog value")
    elif isinstance(value, int) and abs(value) > SAFE_INTEGER:
        raise ValueError("an integer beyond 2**53 - 1 is not a catalog value")
    return value


Json = Annotated[JsonValue, AfterValidator(plain)]
"""JSON data whose numbers canonical JSON can hold."""
