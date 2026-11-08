"""Git and file helpers shared by the checks."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

RELEASE = "platform"
ZERO_SHA = "0" * 40
# The tree of no files, to diff a root commit against.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
# git shows the commits and parents a push sends: no replace refs, no grafts
# (a graft file that cannot exist), no commit-graph file.
GIT_ENV = {"GIT_NO_REPLACE_OBJECTS": "1", "GIT_GRAFT_FILE": "/dev/null/none"}
GIT_CONFIG = {"core.commitGraph": "false"}


def git_environment() -> dict[str, str]:
    """The environment git runs in: the process's, with GIT_ENV and GIT_CONFIG added."""
    env = {**os.environ, **GIT_ENV}
    count = int(env.get("GIT_CONFIG_COUNT", "0") or "0")
    for key, value in GIT_CONFIG.items():
        env[f"GIT_CONFIG_KEY_{count}"] = key
        env[f"GIT_CONFIG_VALUE_{count}"] = value
        count += 1
    env["GIT_CONFIG_COUNT"] = str(count)
    return env


def display_path(path: str) -> str:
    """A path for printing: bytes that are not UTF-8 escaped.

    Args:
        path: A path as ``os.fsdecode`` gives it.

    Returns:
        A printable string.
    """
    return path.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")


def _run(root: Path, args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        env=git_environment(),
        check=False,
    )


class GitError(Exception):
    """A git command failed."""


def git(root: Path, args: Sequence[str]) -> str:
    """Run git in ROOT and return its standard output as text.

    Args:
        root: The working tree.
        args: The git arguments.

    Returns:
        Standard output.

    Raises:
        GitError: git exited with a non-zero status.
    """
    return git_bytes(root, args).decode("utf-8", errors="replace")


def git_bytes(root: Path, args: Sequence[str]) -> bytes:
    """Run git in ROOT and return its standard output as bytes.

    Args:
        root: The working tree.
        args: The git arguments.

    Returns:
        Standard output.

    Raises:
        GitError: git exited with a non-zero status.
    """
    done = _run(root, args)
    if done.returncode != 0:
        message = done.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"git {' '.join(args)}: {message}")
    return done.stdout


def git_ok(root: Path, args: Sequence[str]) -> bool:
    """Whether a git command in ROOT exits 0.

    Args:
        root: The working tree.
        args: The git arguments.

    Returns:
        True when git exited 0.
    """
    return _run(root, args).returncode == 0


def repository_files(root: Path, prefix: str = "") -> list[str]:
    """The repository's files: tracked, and untracked ones git does not ignore.

    Args:
        root: The working tree.
        prefix: Only paths below this directory (relative to ROOT), or all.

    Returns:
        Paths relative to ROOT that exist in the working tree, sorted.
    """
    args = ["ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    if prefix:
        args += ["--", prefix]
    # File names as the file system has them (os.fsdecode), so a name that
    # is not UTF-8 is still found.
    names = {os.fsdecode(n) for n in git_bytes(root, args).split(b"\0") if n}
    return sorted(n for n in names if (root / n).is_file() or (root / n).is_symlink())


def stage_release(root: Path, dest: Path) -> Path:
    """Copy the release's files (those of :func:`repository_files`) to DEST.

    The development environment and caches below ``platform/`` are ignored
    by git, so they are not part of the staged release: the copy is what a
    lab host gets from the repository.

    Args:
        root: The working tree.
        dest: A directory that does not exist yet.

    Returns:
        DEST, holding the release (``platform-manifest.yaml`` at its root).
    """
    dest.mkdir(parents=True)
    for name in repository_files(root, RELEASE):
        source = root / name
        target = dest / Path(name).relative_to(RELEASE)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            target.symlink_to(source.readlink())
        else:
            shutil.copy2(source, target)
    return dest


MAIN = "main"
MAIN_REF = "refs/remotes/origin/main"


def push_range(
    root: Path, branch: str, before: str | None, after: str = "HEAD"
) -> tuple[list[str], str]:
    """The commits a run checks: those not on main yet, or those a push to main brought.

    On a branch other than main: every commit in ``origin/main..AFTER``, so a
    refused commit fails every run until it is resolved, and the commits of
    a push that ran no CI are covered by the next run. On main: the push's
    ``BEFORE..AFTER``; the whole history of AFTER when BEFORE is missing,
    the zero hash, or unknown to git.

    Args:
        root: The working tree.
        branch: The branch pushed to (its short name).
        before: The branch's tip before the push, or None.
        after: The pushed commit.

    Returns:
        The commits (newest first) and a description of the range.

    Raises:
        GitError: origin/main is not fetched, or a git command failed.
    """
    if branch != MAIN:
        if not git_ok(root, ["rev-parse", "--verify", "--quiet", f"{MAIN_REF}^{{commit}}"]):
            raise GitError("origin/main is not fetched: fetch it (the range is origin/main..)")
        commits = git(root, ["rev-list", after, "--not", MAIN_REF]).split()
        return commits, f"origin/main..{after} (branch {branch})"
    if before and before != ZERO_SHA and git_ok(root, ["cat-file", "-e", f"{before}^{{commit}}"]):
        commits = git(root, ["rev-list", after, "--not", before]).split()
        return commits, f"{before[:12]}..{after} (branch {MAIN})"
    return git(root, ["rev-list", after]).split(), f"the whole history of {after} (branch {MAIN})"
