"""Identifiers: ULIDs in their canonical form (26 uppercase Crockford base32 characters)."""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator
from ulid import ULID


def new_id() -> str:
    """A new ULID, time-ordered.

    Returns:
        Its canonical string.
    """
    return str(ULID())


def check_id(value: str) -> str:
    """VALUE, if it is a ULID in canonical form.

    Args:
        value: The identifier.

    Returns:
        VALUE unchanged.

    Raises:
        ValueError: VALUE is not a ULID or not in canonical (uppercase) form.
    """
    if str(ULID.from_str(value)) != value:
        raise ValueError("not a ULID in canonical form")
    return value


Id = Annotated[str, AfterValidator(check_id)]
"""A ULID field of a model."""
