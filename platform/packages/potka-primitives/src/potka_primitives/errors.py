"""What the library raises.

The text of every error here is its reason and nothing else: one word of a
closed list. A step's parameters, a fault primitive's name and the program
that failed are details of an injection, so what an error carries of them
is in its attributes, for the caller's restricted record, and never in its
text. No error here is chained to one of the Incus client's, whose
messages name an instance and the path that was asked for.
"""

from __future__ import annotations

from typing import ClassVar, Final

# Why a target cannot take a fault, or why a fault could not be made.
REASONS: Final = frozenset(
    {
        "adds_execute_bit",
        "already_in_that_state",
        "anchor_not_found",
        "anchor_not_unique",
        "command_failed",
        "container_not_found",
        "file_exists",
        "file_is_executable",
        "file_not_found",
        "inode_count_not_fixed",
        "no_place_for_defect",
        "not_a_container",
        "not_a_regular_file",
        "path_not_on_root_disk",
        "program_exists",
        "root_disk_size_missing",
        "root_disk_usage_missing",
        "too_many_inodes",
        "tool_missing",
        "unexpected_output",
        "unit_not_active",
        "unit_not_found",
    }
)
# What kind of failure the Incus client reported.
FAILURES: Final = frozenset(
    {
        "forbidden",
        "incus_error",
        "not_found",
        "not_sent",
        "operation_failed",
        "operation_timeout",
        "output_lost",
        "unreachable",
        "wrong_kind",
    }
)


class PrimitiveError(Exception):
    """An error of the library; its text is its reason.

    Attributes:
        reason: Why, in one word of the class's closed list.
        alias: The alias of the node it happened on, when there is one.
        program: The program that failed or is missing, when there is one.
        status: That program's exit status, when it ran.
    """

    known: ClassVar[frozenset[str]] = REASONS

    def __init__(
        self,
        reason: str,
        *,
        alias: str | None = None,
        program: str | None = None,
        status: int | None = None,
    ) -> None:
        """Keep the reason as the text and the rest as attributes.

        Raises:
            ValueError: REASON is not of the class's list (it is not shown).
        """
        if reason not in self.known:
            raise ValueError("not a reason of the library's list")
        super().__init__(reason)
        self.reason = reason
        self.alias = alias
        self.program = program
        self.status = status


class Refusal(PrimitiveError):
    """What a primitive or a probe raises inside the library: it cannot go on.

    Under `check`, `ready`, `apply` and a probe's `run` the base classes
    turn it into the error or the verdict of that call. A caller of a
    `Node` itself gets it as it is.
    """


class SetupRefused(PrimitiveError):
    """A target cannot take a fault: found before anything was changed for it."""


class FaultFailed(PrimitiveError):
    """A fault could not be made; what was changed before it failed stays."""


class TargetFailure(PrimitiveError):
    """Incus did not do what was asked: a failure of the infrastructure, never of a model."""

    known: ClassVar[frozenset[str]] = FAILURES


class UnknownName(LookupError):
    """The library has no fault primitive or probe of that name.

    Attributes:
        name: The name asked for.
    """

    def __init__(self, name: str) -> None:
        """Keep the name as an attribute; the text does not hold it."""
        super().__init__("unknown_name")
        self.name = name


class UnknownNode(LookupError):
    """A run has no node of that alias.

    Attributes:
        alias: The alias asked for.
    """

    def __init__(self, alias: str) -> None:
        """Keep the alias as an attribute; the text does not hold it."""
        super().__init__("unknown_node")
        self.alias = alias
