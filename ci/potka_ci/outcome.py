"""The result of a check."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Status = Literal["passed", "failed", "skipped", "warning"]
EXIT_CODES: dict[Status, int] = {"passed": 0, "warning": 0, "failed": 1, "skipped": 2}


@dataclass
class Outcome:
    """What a check found.

    Attributes:
        status: ``passed``; ``warning`` (passed, with something to look at);
            ``failed``; or ``skipped`` (an input is missing).
        lines: What to print: each finding, warning or reason.
    """

    status: Status
    lines: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        """The command line's exit status for this outcome."""
        return EXIT_CODES[self.status]


def from_findings(findings: list[str], warnings: list[str], summary: str) -> Outcome:
    """Build an outcome: failed with findings, else warning or passed.

    Args:
        findings: Problems; any of them fails the check.
        warnings: Things to look at that do not fail it.
        summary: The line printed when nothing failed.

    Returns:
        The outcome.
    """
    if findings:
        return Outcome("failed", [*findings, *warnings])
    if warnings:
        return Outcome("warning", [*warnings, summary])
    return Outcome("passed", [summary])
