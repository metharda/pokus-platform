"""The forms of the values a step's parameters hold.

A parameter reaches a command or a file call as it is given, so its model
bounds it here: an alias, a path in its normal form, a unit's name, a
percentage. Each form is narrow enough that a value can stand alone in a
command, after ``--``, without being read as anything else.
"""

from __future__ import annotations

import posixpath
import re
from typing import Annotated, Final

from pydantic import AfterValidator, Field

# The directories no path of a step may lie in: what is there is no file of a disk.
REFUSED_ROOTS: Final = ("/dev", "/proc", "/sys")
_PATH: Final = re.compile(r"/[!-~]+")
# A unit of the kinds a fault may act on; systemd's own units and the lab's channel
# into a target are refused by name.
UNIT_SUFFIXES: Final = ("service", "socket", "timer")
_UNIT: Final = re.compile(rf"[A-Za-z0-9@._:-]{{1,240}}\.(?:{'|'.join(UNIT_SUFFIXES)})")
PROTECTED_UNITS: Final = (
    "systemd-",
    "dbus",
    "incus-",
    "user@",
    "user-runtime-dir@",
    "getty@",
    "serial-getty@",
    "container-getty@",
    "console-getty.",
    "emergency.",
    "rescue.",
)


def below(path: str, root: str) -> bool:
    """Whether PATH is ROOT or lies below it (both absolute and in their normal form)."""
    return path == root or path.startswith(root.rstrip("/") + "/")


def _path(value: str) -> str:
    if (
        _PATH.fullmatch(value) is None
        or posixpath.normpath(value) != value
        or value.startswith("//")
    ):
        raise ValueError(
            "a path is absolute, in its normal form, of printable characters without a blank,"
            " and not / itself"
        )
    if any(below(value, root) for root in REFUSED_ROOTS):
        raise ValueError("a path does not lie in /dev, /proc or /sys")
    return value


def _unit(value: str) -> str:
    if _UNIT.fullmatch(value) is None or value.startswith("-") or "@." in value:
        raise ValueError(
            "a unit's name is of letters, digits and @._:- and ends in .service, .socket or"
            " .timer; a template's unit has its instance"
        )
    return value


def _unprotected(value: str) -> str:
    if value.startswith(PROTECTED_UNITS):
        raise ValueError("a unit of systemd's own or of the lab's channel is not a fault's")
    return value


Alias = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]{0,14}$")]
"""A node's neutral alias."""
AbsolutePath = Annotated[str, Field(max_length=4096), AfterValidator(_path)]
"""An absolute path in its normal form; never ``/`` and never in ``/dev``, ``/proc``, ``/sys``."""
Unit = Annotated[str, AfterValidator(_unit), AfterValidator(_unprotected)]
"""A service, socket or timer unit that a fault may act on."""
Percent = Annotated[int, Field(ge=1, le=100)]
"""A percentage from 1 to 100."""
