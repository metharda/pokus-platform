"""The programs that are shells: made to read a command line.

One list for everything that keeps a shell off a command: the tool
registry's templates, a run's context, a blueprint description's validators
and the fault primitives. The list is of shells only; a program that can
run a text it is given (an interpreter with an option for it) is not on it.
"""

from __future__ import annotations

from typing import Final

SHELLS: Final = frozenset(
    {
        "ash",
        "bash",
        "busybox",
        "csh",
        "dash",
        "fish",
        "hush",
        "ksh",
        "lksh",
        "mksh",
        "pdksh",
        "posh",
        "rbash",
        "sash",
        "sh",
        "tcsh",
        "yash",
        "zsh",
    }
)


def is_shell(word: str) -> bool:
    """Whether WORD names a shell.

    Args:
        word: A word of a command: a program's name or path, or an argument.

    Returns:
        True when the word's part after its last slash is a shell's name,
        so ``sh`` and ``/usr/bin/sh`` both are.
    """
    return word.rsplit("/", 1)[-1] in SHELLS
