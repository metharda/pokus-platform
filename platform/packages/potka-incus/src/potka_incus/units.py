"""Sizes as Incus writes them: a whole number and a unit.

Incus reads ``8GiB``, ``500MB`` or ``1024`` (bytes) in its configuration,
with decimal units of 1000 (kB, MB, GB, TB, PB, EB) and binary units of
1024 (KiB, MiB, GiB, TiB, PiB, EiB); the rules here are those of its
``ParseByteSizeString``, which gives a signed number of 64 bits. A size in
any other form, or one too large for that, is refused, never guessed.
"""

from __future__ import annotations

import re

_DECIMAL = ("kB", "MB", "GB", "TB", "PB", "EB")
_BINARY = ("KiB", "MiB", "GiB", "TiB", "PiB", "EiB")
UNITS: dict[str, int] = {
    "": 1,
    "B": 1,
    " bytes": 1,
    **{unit: 1000**power for power, unit in enumerate(_DECIMAL, start=1)},
    **{unit: 1024**power for power, unit in enumerate(_BINARY, start=1)},
}
_SIZE_RE = re.compile(r"(?P<number>[0-9]+)(?P<unit>[^0-9].*)?", flags=re.DOTALL)
MAX_SIZE = 2**63 - 1


def parse_size(text: str) -> int:
    """The number of bytes TEXT stands for.

    Args:
        text: A size as Incus's configuration holds it.

    Returns:
        The size in bytes.

    Raises:
        ValueError: TEXT is not digits followed by one of Incus's units,
            or stands for more bytes than Incus can hold.
    """
    match = _SIZE_RE.fullmatch(text)
    if match is None or (match.group("unit") or "") not in UNITS:
        raise ValueError(f"not a size as Incus writes one: {text!r}")
    size = int(match.group("number")) * UNITS[match.group("unit") or ""]
    if size > MAX_SIZE:
        raise ValueError(f"not a size as Incus writes one: {text!r}")
    return size
