"""Restricted values: ground truth, the scenario of an unsealed run, injection details.

A restricted value is held in a `Restricted` wrapper and taken out only by
`reveal()`. The wrapper never shows the value: `str`, `repr` and format
strings give ``<restricted>``, it cannot be pickled or serialized, JSON
encoders, canonical JSON and pydantic's JSON values refuse it, and the log
formatter redacts it. It guards against accidents; a service that reveals
a value and writes it out still leaks it.
"""

from __future__ import annotations

import copy
from typing import Any, Final, NoReturn, SupportsIndex

MARKER: Final = "<restricted>"


class Restricted[T]:
    """A value that never shows itself."""

    __slots__ = ("__value",)

    def __init__(self, value: T) -> None:
        """Hold VALUE.

        Args:
            value: The restricted value.
        """
        self.__value = value

    def reveal(self) -> T:
        """The value itself, for the code that is allowed to use it.

        Returns:
            The value.
        """
        return self.__value

    def __repr__(self) -> str:
        """``<restricted>``."""
        return MARKER

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        """``<restricted>``, whatever the format specification."""
        return MARKER

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        """Refuse pickling: a pickle would carry the value."""
        raise TypeError("a restricted value cannot be pickled")

    def __reduce__(self) -> NoReturn:
        """Refuse pickling: a pickle would carry the value."""
        raise TypeError("a restricted value cannot be pickled")

    def __getstate__(self) -> NoReturn:
        """Refuse a serializer's fallback to the object's state."""
        raise TypeError("a restricted value cannot be serialized")

    def __copy__(self) -> Restricted[T]:
        """The same restricted value (it is never changed)."""
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> Restricted[T]:
        """A restricted copy of a deep copy of the value."""
        return Restricted(copy.deepcopy(self.__value, memo))

    def __eq__(self, other: object) -> bool:
        """Equal to another restricted value holding an equal value."""
        return isinstance(other, Restricted) and self.__value == other.reveal()

    def __hash__(self) -> int:
        """A hash of the value mixed with a constant, so a restricted value can be a key."""
        return hash((MARKER, self.__value))
