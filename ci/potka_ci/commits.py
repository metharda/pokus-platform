"""The commit check: every commit a push introduces is the maintainer's.

The expected identity is the author of the repository's root commit
(``ROOT_COMMIT``), read from git when the check runs, so no identity is
written in the repository; a history with any other root is refused. The
commits checked are those of :func:`potka_ci.repo.push_range`: on a branch
other than main, and in a run without a push event, every commit not on
main yet (``origin/main..AFTER``); on a push to main, the push's
``BEFORE..AFTER`` (the whole history without a known ``BEFORE``). A
shallow checkout is refused: its cut-off history could hide commits.

A merge made on github.com is accepted: two or more parents, the expected
identity as its author, GitHub as its committer; it may carry GitHub's
signature. Every other commit has the expected identity as its author and
its committer and no signature. No commit declares an encoding, holds a
NUL byte or has a message line that matches one of the patterns of
``ci/attribution-patterns`` (``grep -i -E`` in the C locale); a pattern grep
cannot use fails the check. Findings name the commit and the rule, not the
identities.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from potka_ci.outcome import Outcome, from_findings
from potka_ci.repo import GitError, git, git_bytes, push_range

PATTERNS_FILE = Path(__file__).resolve().parents[1] / "attribution-patterns"
# The repository's root commit: its author is the expected identity.
ROOT_COMMIT = "d965926f816f145216a7c6a8e28968675196ed2f"
GITHUB = ("GitHub", "noreply@github.com")
MERGE_PARENTS = 2
PERSON_RE = re.compile(r"^(?P<name>.*) <(?P<email>[^<>]*)> -?\d+ [+-]\d{4}$")


class PatternError(Exception):
    """The patterns file holds a pattern the check cannot use."""


@dataclass(frozen=True)
class Commit:
    """What the check reads from one raw commit object.

    Attributes:
        sha: The commit.
        parents: How many parents it has.
        author: ``(name, email)``, or None if the header could not be read.
        committer: ``(name, email)``, or None if the header could not be read.
        signed: Whether it carries a signature header.
        encoding: Whether it declares an encoding.
        message: The raw message, or None when the object is not UTF-8 text
            or holds a NUL byte.
    """

    sha: str
    parents: int
    author: tuple[str, str] | None
    committer: tuple[str, str] | None
    signed: bool
    encoding: bool
    message: str | None


def load_patterns(path: Path = PATTERNS_FILE) -> list[str]:
    """The patterns: every line that is not empty or a comment.

    Args:
        path: The patterns file.

    Returns:
        The patterns.

    Raises:
        PatternError: A line holds a carriage return, a pattern is not usable
            by grep, or there is no pattern.
    """
    patterns = []
    for line in path.read_bytes().decode("utf-8").split("\n"):
        if "\r" in line:
            raise PatternError(f"{path.name}: a carriage return (the file must have LF endings)")
        if line.strip() and not line.lstrip().startswith("#"):
            patterns.append(line)
    if not patterns:
        raise PatternError(f"{path.name}: no pattern")
    for pattern in patterns:
        if _grep(pattern, b"") not in (0, 1):
            raise PatternError(f"{path.name}: grep cannot use the pattern {pattern!r}")
    return patterns


def _grep(pattern: str, data: bytes) -> int:
    done = subprocess.run(
        ["grep", "-q", "-i", "-E", "-e", pattern],
        input=data,
        capture_output=True,
        env={**os.environ, "LC_ALL": "C"},
        check=False,
    )
    return done.returncode


def matching_pattern(message: str, patterns: list[str]) -> str | None:
    """The first pattern that matches a line of MESSAGE.

    Args:
        message: The raw message.
        patterns: The patterns.

    Returns:
        The pattern, or None.

    Raises:
        PatternError: grep could not use a pattern.
    """
    data = message.encode("utf-8")
    for pattern in patterns:
        status = _grep(pattern, data)
        if status == 0:
            return pattern
        if status != 1:
            raise PatternError(f"grep cannot use the pattern {pattern!r}")
    return None


def _person(value: bytes) -> tuple[str, str] | None:
    try:
        m = PERSON_RE.match(value.decode("utf-8"))
    except UnicodeDecodeError:
        return None
    return (m.group("name"), m.group("email")) if m else None


def read_commit(root: Path, sha: str) -> Commit:
    """Read one commit from its raw object.

    Args:
        root: The working tree.
        sha: The commit.

    Returns:
        What the check needs of it.
    """
    raw = git_bytes(root, ["cat-file", "commit", sha])
    header, _, body = raw.partition(b"\n\n")
    fields: dict[bytes, list[bytes]] = {}
    keys: list[bytes] = []
    for line in header.split(b"\n"):
        if line.startswith(b" "):
            continue  # a continuation line (of a signature)
        key, _, value = line.partition(b" ")
        keys.append(key)
        fields.setdefault(key, []).append(value)
    # git's parents are the "parent" lines right after "tree", nowhere else.
    parents = 0
    for key in keys[1:]:
        if key != b"parent":
            break
        parents += 1
    message: str | None
    try:
        message = None if b"\0" in raw else body.decode("utf-8")
    except UnicodeDecodeError:
        message = None
    authors, committers = fields.get(b"author", []), fields.get(b"committer", [])
    return Commit(
        sha=sha,
        parents=parents if keys[:1] == [b"tree"] else 0,
        author=_person(authors[0]) if len(authors) == 1 else None,
        committer=_person(committers[0]) if len(committers) == 1 else None,
        signed=any(k.startswith(b"gpgsig") for k in fields),
        encoding=b"encoding" in fields,
        message=message,
    )


def check_commit(commit: Commit, expected: tuple[str, str], patterns: list[str]) -> list[str]:
    """Every reason to refuse one commit.

    Args:
        commit: The commit.
        expected: The expected identity, ``(name, email)``.
        patterns: The attribution patterns.

    Returns:
        The findings.
    """
    short = commit.sha[:12]
    found = []
    github_merge = (
        commit.parents >= MERGE_PARENTS and commit.author == expected and commit.committer == GITHUB
    )
    if commit.author != expected:
        found.append(f"commit {short}: the author is not the expected identity")
    if commit.committer != expected and not github_merge:
        found.append(f"commit {short}: the committer is not the expected identity")
    if commit.signed and not github_merge:
        found.append(f"commit {short}: a signed commit (commits are unsigned)")
    if commit.encoding:
        found.append(f"commit {short}: the commit declares an encoding")
    if commit.message is None:
        found.append(f"commit {short}: the commit object is not UTF-8 text or holds a NUL byte")
    else:
        pattern = matching_pattern(commit.message, patterns)
        if pattern is not None:
            found.append(f"commit {short}: a message line matches the pattern {pattern!r}")
    return found


def expected_identity(root: Path, after: str, root_commit: str = ROOT_COMMIT) -> tuple[str, str]:
    """The author of the repository's root commit, which AFTER's history must have.

    Args:
        root: The working tree.
        after: The pushed commit.
        root_commit: The repository's root commit.

    Returns:
        ``(name, email)``.

    Raises:
        GitError: AFTER's history has another root, or more than one.
    """
    roots = git(root, ["rev-list", "--max-parents=0", after]).split()
    if roots != [root_commit]:
        raise GitError(
            f"the history has the root commit(s) {', '.join(r[:12] for r in roots) or 'none'},"
            f" not the repository's root commit {root_commit[:12]}"
        )
    name, _, email = (
        git(root, ["log", "-1", "--format=%an%x00%ae", root_commit]).strip().partition("\0")
    )
    return name, email


def run(
    root: Path,
    branch: str,
    before: str | None,
    after: str = "HEAD",
    root_commit: str = ROOT_COMMIT,
    pushed: bool = True,
) -> Outcome:
    """Check the commits of the run's range.

    Args:
        root: The working tree.
        branch: The branch pushed to.
        before: The branch's tip before the push, or None.
        after: The pushed commit.
        root_commit: The repository's root commit.
        pushed: Whether the run is a push event's.

    Returns:
        The outcome.
    """
    if git(root, ["rev-parse", "--is-shallow-repository"]).strip() != "false":
        return Outcome("failed", ["a shallow checkout: fetch the whole history"])
    try:
        patterns = load_patterns()
        expected = expected_identity(root, after, root_commit)
        commits, scope = push_range(root, branch, before, after, pushed)
        findings = [
            f for sha in commits for f in check_commit(read_commit(root, sha), expected, patterns)
        ]
    except (PatternError, GitError) as exc:
        return Outcome("failed", [str(exc)])
    return from_findings(findings, [], f"commits: {len(commits)} commit(s) checked ({scope})")
