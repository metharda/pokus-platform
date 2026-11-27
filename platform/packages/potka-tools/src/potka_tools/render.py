"""The command of a checked call: a list of arguments, made from the tool's template.

A command is a tuple of strings and never a line of text: each element of
the template becomes exactly one element of the command, a literal as it
is, a placeholder as its argument's value, an option as its literal and the
value together. No value is split or quoted, no two elements are joined and
none is left out, so no value can become a second argument or a piece of
syntax.

Every command runs under ``timeout``, which ends it after the tool's
``timeout_s`` and kills it ``kill_after_s`` later:
``timeout --kill-after=<k>s <t>s <program> <arguments...>``. The command
that checks a tool afterwards is made the same way, with the same limits.

Only a :class:`potka_tools.arguments.Call` is rendered. A call that
`potka_tools.arguments.check` returned has been checked whole, and
rendering it cannot fail; a call made any other way is not rendered here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

from potka_tools.arguments import Call
from potka_tools.registry import Exec
from potka_tools.template import Marked, elements

TIMEOUT: Final = "timeout"


@dataclass(frozen=True)
class Command:
    """What is run for a call.

    Attributes:
        on: Where: in the node the call names, or on the run's probe client.
        argv: The program and its arguments, under ``timeout``.
        post_check: The command that checks the tool afterwards, under
            ``timeout`` too, or None when the tool has none. It runs where
            the command runs.
        timeout_s: The seconds after which ``timeout`` ends a command.
        kill_after_s: The seconds after that at which it kills it.
        max_output_bytes: The most bytes of each output stream that the
            model is given.
    """

    on: Literal["node", "probe_client"]
    argv: tuple[str, ...]
    post_check: tuple[str, ...] | None
    timeout_s: int
    kill_after_s: int
    max_output_bytes: int


def _made(
    template: tuple[str | Marked, ...], values: Mapping[str, str], limits: Exec
) -> tuple[str, ...]:
    """TEMPLATE with each argument's value in its place, under ``timeout``."""
    made = tuple(
        one.literal + (values[one.argument] if one.argument is not None else "")
        for one in elements(template)
    )
    return (TIMEOUT, f"--kill-after={limits.kill_after_s}s", f"{limits.timeout_s}s", *made)


def render(call: Call) -> Command | None:
    """The command of CALL, or None when a handler carries the tool out.

    Args:
        call: A checked call.

    Returns:
        The command and the command that checks it, each a tuple with one
        element for each element of its template after the three of
        ``timeout``.
    """
    said = call.tool.command
    if said is None:
        return None
    check = call.tool.post_check
    return Command(
        on=said.on,
        argv=_made(said.argv, call.values, said),
        post_check=None if check is None else _made(check.argv, call.values, said),
        timeout_s=said.timeout_s,
        kill_after_s=said.kill_after_s,
        max_output_bytes=said.max_output_bytes,
    )
