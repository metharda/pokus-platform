"""Canonical JSON: the JSON Canonicalization Scheme of RFC 8785 (JCS).

Every hash and signature over JSON (the event hash chain, the run manifest,
attestations) is taken over these bytes: UTF-8, no whitespace, object keys
sorted by their UTF-16 code units, numbers as ECMAScript prints them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import rfc8785

type JSONValue = bool | int | float | str | Sequence[JSONValue] | Mapping[str, JSONValue] | None


SAFE_INTEGER = 2**53 - 1
# The messages never show the value: it may be one a log must not hold.
INTEGER = "an integer beyond 2**53 - 1 that is not the canonical text of a JSON number"


class CanonicalJSONError(ValueError):
    """A value has no canonical JSON form."""


def _as_numbers(value: JSONValue) -> JSONValue:
    """VALUE with every integer beyond 2**53 - 1 as the double it stands for.

    A JSON number is an IEEE 754 double, printed with the fewest digits
    that read back as it: canonical JSON of the float 4e16 reads back as an
    integer. Such an integer is accepted when its digits are exactly the
    canonical text of the double they read as, so canonical JSON read back
    canonicalizes to the same bytes; any other integer beyond 2**53 - 1
    (2**53 + 1, for example) would change its value and is refused.
    """
    if isinstance(value, Mapping):
        return {key: _as_numbers(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_as_numbers(item) for item in value]
    if isinstance(value, bool) or not isinstance(value, int) or abs(value) <= SAFE_INTEGER:
        return value
    try:
        number = float(value)
        canonical = rfc8785.dumps(number) == str(value).encode()
    except (OverflowError, ValueError) as exc:
        raise CanonicalJSONError(INTEGER) from exc
    if not canonical:
        raise CanonicalJSONError(INTEGER)
    return number


def canonical_json(value: JSONValue) -> bytes:
    """The RFC 8785 canonical bytes of VALUE.

    Args:
        value: JSON data: None, booleans, numbers, strings, lists and
            dictionaries with string keys.

    Returns:
        The canonical UTF-8 bytes.

    Raises:
        CanonicalJSONError: VALUE holds NaN or an infinity, an integer beyond
            2**53 - 1 whose digits are not the canonical text of the double
            they read as (2**53 + 1 and 2**68, for example), a lone
            surrogate (in a key or a value), a key that is not a string, or a
            type JSON does not have. The message never shows the value.
    """
    try:
        return rfc8785.dumps(_as_numbers(value))
    except rfc8785.IntegerDomainError as exc:
        raise CanonicalJSONError(INTEGER) from exc
    except rfc8785.FloatDomainError as exc:
        raise CanonicalJSONError("NaN or an infinity has no JSON form") from exc
    except rfc8785.CanonicalizationError as exc:
        raise CanonicalJSONError(str(exc)) from exc
    except UnicodeError as exc:
        raise CanonicalJSONError("a string holds a lone surrogate") from exc
