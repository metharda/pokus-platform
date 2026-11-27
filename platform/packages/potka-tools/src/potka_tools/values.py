"""The characters a value may hold, and paths as the tools see them.

A string and a path are printable ASCII: no character that a terminal, a
log or the screen of a person who approves a call shows otherwise than it
is. A text (a patch, a reason) may hold any printing character of
Unicode, the line feed and the tab.

A path is absolute and compared in its normal form, by whole components:
``/etc/nginx2`` is not below ``/etc/nginx``.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Final

PRINTABLE_RE: Final = re.compile(r"[\x20-\x7e]+")
# To Incus a device or a pipe is a file, and reading one may never end: no tool is given
# a path in these, whatever else allows it.
REFUSED_ROOTS: Final = ("/dev", "/proc", "/sys")
# The most bytes of one component of a path, as Linux has it.
COMPONENT_MAX: Final = 255


def printable(value: str) -> bool:
    """Whether VALUE is not empty and holds only printable ASCII (U+0020 to U+007E)."""
    return PRINTABLE_RE.fullmatch(value) is not None


def is_text(value: str) -> bool:
    """Whether VALUE is not empty and holds only what a text may.

    Refused: every character of Unicode's categories C (control, format,
    surrogate, private use, unassigned), Zl and Zp, but the line feed and
    the tab.
    """
    for character in value:
        if character in "\n\t":
            continue
        category = unicodedata.category(character)
        if category[0] == "C" or category in ("Zl", "Zp"):
            return False
    return bool(value)


def is_normal(path: str) -> bool:
    """Whether PATH is absolute and in its normal form, and not ``/`` itself.

    The normal form has no ``.``, no ``..``, no doubled and no final slash,
    and no component of more than 255 bytes: it is what `normalize` gives.
    """
    return path != "/" and normalize(path) == path


def normalize(path: str) -> str | None:
    """The normal form of the absolute PATH, or None when it has none.

    Doubled and final slashes and ``.`` go; ``..`` takes the component
    before it away, by the path's text alone. A path that is not absolute,
    climbs above ``/`` or has a component of more than 255 bytes has no
    normal form. What a component is in a file system (a link) is not
    looked at: the normal form is what is handed on, so ``..`` never is.
    """
    if not path.startswith("/"):
        return None
    parts: list[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        elif len(part.encode("utf-8", "surrogatepass")) > COMPONENT_MAX:
            return None
        else:
            parts.append(part)
    return "/" + "/".join(parts)


def below(path: str, roots: Iterable[str]) -> bool:
    """Whether PATH lies below one of ROOTS, by whole components; a root itself does not."""
    return any(path.startswith(root + "/") for root in roots)


def within(path: str, roots: Iterable[str]) -> bool:
    """Whether PATH is one of ROOTS or lies below one, by whole components."""
    return any(path == root or path.startswith(root + "/") for root in roots)


def refused(path: str) -> bool:
    """Whether PATH is one of :data:`REFUSED_ROOTS` or lies below one."""
    return within(path, REFUSED_ROOTS)
