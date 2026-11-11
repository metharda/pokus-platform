"""The branch and the commits a run checks, read from the environment.

In GitHub Actions (``GITHUB_ACTIONS=true``) the branch is ``GITHUB_REF_NAME``.
On a push event, before and after are the event's own, read from the event
file (``GITHUB_EVENT_PATH``). On any other event (a manual run) there is no
before, and after is ``GITHUB_SHA``. Elsewhere (the local gate) the branch
is the checked-out one (``HEAD`` when detached), with no before and
``HEAD`` as after. Only a push event's run is marked as pushed: the others
check ``origin/main..AFTER`` on every branch, main included.

These values never pass through make or a shell. They are checked here
(the branch with ``git check-ref-format``, before and after as 40 hex
digits) and handed to git only as arguments, so a branch named
``x$(cmd)`` is a name, never a command.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from potka_ci.outcome import Outcome
from potka_ci.repo import GitError, git, git_ok

SHA_RE = re.compile(r"[0-9a-f]{40}")


class InputError(Exception):
    """An input of the run is missing or malformed."""


@dataclass(frozen=True)
class Push:
    """What a run checks.

    Attributes:
        branch: The branch pushed to (its short name).
        before: The branch's tip before the push, or None.
        after: The pushed commit.
        pushed: Whether the run is a push event's.
    """

    branch: str
    before: str | None
    after: str
    pushed: bool


def _branch(root: Path, name: str, what: str) -> str:
    if not name or not git_ok(root, ["check-ref-format", f"refs/heads/{name}"]):
        raise InputError(f"{what} is not a valid branch name")
    return name


def _sha(value: object, what: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise InputError(f"{what} is not a commit hash (40 hex digits)")
    return value


def _event(environ: Mapping[str, str]) -> dict[str, object]:
    path = environ.get("GITHUB_EVENT_PATH", "")
    if not path:
        raise InputError("GITHUB_EVENT_PATH is not set: the push event cannot be read")
    try:
        event = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InputError(f"the push event cannot be read: {exc}") from exc
    if not isinstance(event, dict):
        raise InputError("the push event is not a JSON object")
    return event


def push_inputs(root: Path, environ: Mapping[str, str]) -> Push:
    """The branch, before and after of this run.

    Args:
        root: The working tree.
        environ: The environment.

    Returns:
        The checked inputs.

    Raises:
        InputError: An input is missing or malformed.
        GitError: git could not name the checked-out branch.
    """
    if environ.get("GITHUB_ACTIONS") != "true":
        branch = git(root, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
        return Push(_branch(root, branch, "the checked-out branch"), None, "HEAD", pushed=False)
    branch = _branch(root, environ.get("GITHUB_REF_NAME", ""), "GITHUB_REF_NAME")
    if environ.get("GITHUB_EVENT_NAME") == "push":
        event = _event(environ)
        before = _sha(event.get("before"), "the push event's before")
        after = _sha(event.get("after"), "the push event's after")
        return Push(branch, before, after, pushed=True)
    return Push(branch, None, _sha(environ.get("GITHUB_SHA"), "GITHUB_SHA"), pushed=False)


class Check(Protocol):
    """``privacy.run`` or ``commits.run``."""

    def __call__(
        self, root: Path, branch: str, before: str | None, after: str, *, pushed: bool
    ) -> Outcome:
        """Check the run's range (:func:`potka_ci.repo.push_range`)."""


def run_on_push(root: Path, environ: Mapping[str, str], check: Check) -> Outcome:
    """Run a check on this run's branch and range.

    Args:
        root: The working tree.
        environ: The environment.
        check: ``privacy.run`` or ``commits.run``.

    Returns:
        The check's outcome; failed when an input is missing or malformed.
    """
    try:
        push = push_inputs(root, environ)
    except (InputError, GitError) as exc:
        return Outcome("failed", [str(exc)])
    return check(root, push.branch, push.before, push.after, pushed=push.pushed)
