"""What every command of a checked call must be, for the tests of potka-tools."""

from __future__ import annotations

from typing import Any

from potka_tools.registry import Tool
from potka_tools.render import TIMEOUT
from potka_tools.template import Marked, elements


def shape(
    used: Tool, written: tuple[str | Marked, ...], argv: tuple[str, ...], values: Any
) -> None:
    """ARGV is WRITTEN, element for element, under timeout; nothing alone reads as an option."""
    assert used.command is not None
    assert argv[:3] == (
        TIMEOUT,
        f"--kill-after={used.command.kill_after_s}s",
        f"{used.command.timeout_s}s",
    )
    made = argv[3:]
    assert len(made) == len(written) and all(type(one) is str for one in argv)
    assert made[0] == written[0]
    for one, got in zip(elements(written), made, strict=True):
        if one.argument is None:
            assert got == one.literal
        else:
            assert got == one.literal + values[one.argument]
            assert not (one.alone and (not got or got.startswith("-")))
