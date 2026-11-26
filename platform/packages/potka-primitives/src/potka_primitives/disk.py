"""The faults and probes of a full disk.

How full a root disk is, is measured as Incus measures it: the bytes in
use by the instance's state, against the size configured for the root
disk. Nothing here reads ``df``: a file system's own numbers mean something
else on every storage pool driver. A root disk without a configured size,
or whose use Incus does not give, is one the measure cannot be taken on,
and that is said; it is never read as empty.

The measure is taken on a container only. For a virtual machine Incus
counts what the disk's volume takes on the host, which an allocation
inside the guest need not move and a deleted file need not give back; a
fault or a probe on such a root disk is refused until that is known.

A fill takes the use once, just before it allocates, and reads it once
more at the end: it rests on the state's use being current when it is
read. What it reports holds both the share it aimed at and the use it
read, so that a use that lags behind shows in the record.

Inodes have no such measure outside the target, so they are read inside
it, and only where the file system has a fixed number of them.
"""

from __future__ import annotations

import posixpath
import re
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, Field

from potka_primitives.base import (
    Effects,
    Facts,
    Params,
    Primitive,
    Probe,
    Program,
    Requires,
    Verdict,
)
from potka_primitives.context import Node, Ran, Targets
from potka_primitives.errors import Refusal
from potka_primitives.values import AbsolutePath, Percent, below

MEBIBYTE: Final = 1024**2
# A file grows to its length in this many commands at most, none smaller than SMALLEST.
STEPS: Final = 16
SMALLEST: Final = 4 * MEBIBYTE
# How a program says that the disk is full, under the locale C, and the error it names.
FULL: Final = {"No space left on device": "ENOSPC", "Disk quota exceeded": "EDQUOT"}
# The file system types whose number of inodes is fixed when they are made, as
# ``stat --file-system`` names them.
FIXED_INODES: Final = frozenset({"ext2/ext3"})
# The files one command makes, and the most a fault may have to make.
BATCH: Final = 2000
MOST_FILES: Final = 2_000_000
POSTGRESQL: Final = "/var/lib/postgresql"
_MOUNT: Final = re.compile(r"(/[^\n]*)\n")
_INODES: Final = re.compile(r"([0-9]{1,19}):([0-9]{1,19}):([a-z0-9/._-]{1,32})\n")

FINDMNT: Final = Program("findmnt", "util-linux")
FALLOCATE: Final = Program("fallocate", "util-linux")
STAT: Final = Program("stat", "coreutils")
MKDIR: Final = Program("mkdir", "coreutils")
TOUCH: Final = Program("touch", "coreutils")


def share(whole: int, percent: int) -> int:
    """PERCENT of WHOLE, rounded up."""
    return -(-whole * percent // 100)


def full(ran: Ran) -> str | None:
    """The error of a full disk that a failed command names, or None."""
    return next((error for words, error in FULL.items() if ran.said(words)), None)


def on_root_disk(node: Node, directory: str) -> None:
    """Refuse a directory that is not there or that is not on the root file system.

    Raises:
        Refusal: ``file_not_found``, ``path_not_on_root_disk``, or
            ``unexpected_output`` when the mount is not said in one line.
    """
    if node.kind(directory) != "directory":
        raise Refusal("file_not_found")
    ran = node.run(
        ["findmnt", "--noheadings", "--output", "TARGET", f"--target={directory}"]
    ).expect(0)
    said = _MOUNT.fullmatch(ran.text())
    if said is None:
        raise Refusal("unexpected_output", program="findmnt", status=0)
    if said[1] != "/":
        raise Refusal("path_not_on_root_disk")


def measured(node: Node) -> tuple[int, int]:
    """The root disk's configured size and its use, in bytes.

    Raises:
        Refusal: ``not_a_container``, ``root_disk_size_missing`` or
            ``root_disk_usage_missing``.
    """
    if not node.is_container():
        raise Refusal("not_a_container")
    size = node.root_size()
    if size is None or size <= 0:
        raise Refusal("root_disk_size_missing")
    used = node.root_usage()
    if used is None:
        raise Refusal("root_disk_usage_missing")
    return size, used


def inodes(node: Node, path: str) -> tuple[int, int]:
    """The inodes of the file system that holds PATH: how many in all, how many in use.

    Raises:
        Refusal: ``inode_count_not_fixed`` for a file system that makes
            inodes as it needs them; ``unexpected_output`` for an answer
            of another form.
    """
    ran = node.run(["stat", "--file-system", "--format=%c:%d:%T", "--", path]).expect(0)
    said = _INODES.fullmatch(ran.text())
    if said is None:
        raise Refusal("unexpected_output", program="stat", status=0)
    total, free, kind = int(said[1]), int(said[2]), said[3]
    if kind not in FIXED_INODES or total <= 0:
        raise Refusal("inode_count_not_fixed")
    if free > total:
        raise Refusal("unexpected_output", program="stat", status=0)
    return total, total - free


class FillParams(Params):
    """Where a file is allocated, and how full the root disk becomes."""

    path: AbsolutePath
    """The file the fault makes; its directory is there and on the root disk."""
    percent: Percent
    """The share of the root disk's configured size that is in use afterwards."""


class _Fill[P: FillParams](Primitive[P]):
    """Fill the root disk to a percentage of its configured size with one allocated file."""

    requires = Requires((FINDMNT, FALLOCATE))
    reports = frozenset({"size_bytes", "target_bytes", "usage_bytes", "steps", "stopped_by"})
    # What stopped the allocation: the file reached its whole length, or the disk was full.
    words = frozenset({"length", *FULL.values()})

    def describe(self, params: P) -> str:
        """One sentence that says what the step does with these parameters."""
        return (
            f"Allocates the file {params.path} on the node {params.node} until"
            f" {params.percent} % of its root disk's configured size is in use."
        )

    def side_effects(self, params: P) -> Effects:
        """The one file it makes."""
        return Effects(files=(params.path,))

    def _suited(self, node: Node, params: P) -> None:
        measured(node)
        on_root_disk(node, posixpath.dirname(params.path))

    def _missing(self, node: Node, params: P) -> tuple[int, int]:
        """The root disk's size, and the bytes still to take to reach the target."""
        size, used = measured(node)
        return size, share(size, params.percent) - used

    def _ready(self, node: Node, params: P) -> None:
        if node.exists(params.path):
            raise Refusal("file_exists")
        if self._missing(node, params)[1] <= 0:
            raise Refusal("already_in_that_state")

    def _apply(self, node: Node, params: P) -> Facts:
        size, missing = self._missing(node, params)
        if missing <= 0:
            raise Refusal("already_in_that_state")
        step = max(-(-missing // STEPS), SMALLEST)
        length, steps, stopped = 0, 0, "length"
        while length < missing:
            length = min(length + step, missing)
            ran = node.run(["fallocate", f"--length={length}", "--", params.path])
            steps += 1
            if ran.exit_code != 0:
                error = full(ran)
                if error is None:
                    raise Refusal("command_failed", program="fallocate", status=ran.exit_code)
                stopped = error
                break
        _, used = measured(node)
        return {
            "size_bytes": size,
            "target_bytes": share(size, params.percent),
            "usage_bytes": used,
            "steps": steps,
            "stopped_by": stopped,
        }


class DiskFill(_Fill[FillParams]):
    """``disk.fill``: fill a node's root disk."""

    name = "disk.fill"
    version = 1
    Params = FillParams


def _of_postgresql(path: str) -> str:
    """A file below ``/var/lib/postgresql``."""
    if not below(path, POSTGRESQL) or path == POSTGRESQL:
        raise ValueError("the file lies below /var/lib/postgresql")
    return path


class PgFillParams(FillParams):
    """As `FillParams`, with the file among PostgreSQL's own."""

    path: Annotated[AbsolutePath, AfterValidator(_of_postgresql)]
    """The file the fault makes, below ``/var/lib/postgresql``."""


class PgFillDisk(_Fill[PgFillParams]):
    """``pg.fill_disk``: fill the root disk from where PostgreSQL keeps its data."""

    name = "pg.fill_disk"
    version = 1
    Params = PgFillParams
    requires = Requires((FINDMNT, FALLOCATE), service="postgresql")


class InodeParams(Params):
    """Where files are made, how they are named, and how many inodes are in use afterwards."""

    path: AbsolutePath
    """The directory the fault makes for its files; the directory above it is there."""
    prefix: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.]{0,15}$")]
    """How each file's name begins; a number of seven digits follows."""
    percent: Percent
    """The share of the file system's inodes that is in use afterwards."""


class InodeExhaust(Primitive[InodeParams]):
    """``disk.inode_exhaust``: use up the inodes of a node's root file system with empty files."""

    name = "disk.inode_exhaust"
    version = 1
    Params = InodeParams
    requires = Requires((FINDMNT, STAT, MKDIR, TOUCH))
    reports = frozenset({"inodes_total", "inodes_used", "stopped_by"})
    # What stopped the files: their count was reached, or the file system was full.
    words = frozenset({"count", *FULL.values()})

    def describe(self, params: InodeParams) -> str:
        """One sentence that says what the step does with these parameters."""
        return (
            f"Makes empty files in a new directory {params.path} on the node {params.node}"
            f" until {params.percent} % of the inodes of its root file system are in use."
        )

    def side_effects(self, params: InodeParams) -> Effects:
        """The directory it makes, with the files in it."""
        return Effects(files=(params.path,))

    def _suited(self, node: Node, params: InodeParams) -> None:
        above = posixpath.dirname(params.path)
        on_root_disk(node, above)
        inodes(node, above)

    def _missing(self, node: Node, params: InodeParams) -> int:
        """The files to make: what is missing to the target, less the directory itself."""
        total, used = inodes(node, posixpath.dirname(params.path))
        return share(total, params.percent) - used - 1

    def _ready(self, node: Node, params: InodeParams) -> None:
        if node.exists(params.path):
            raise Refusal("file_exists")
        missing = self._missing(node, params)
        if missing <= 0:
            raise Refusal("already_in_that_state")
        if missing > MOST_FILES:
            raise Refusal("too_many_inodes")

    def _apply(self, node: Node, params: InodeParams) -> Facts:
        missing = self._missing(node, params)
        if not 0 < missing <= MOST_FILES:
            raise Refusal("already_in_that_state" if missing <= 0 else "too_many_inodes")
        node.run(["mkdir", "--", params.path]).expect(0)
        stopped = "count"
        for first in range(1, missing + 1, BATCH):
            last = min(first + BATCH, missing + 1)
            names = [f"{params.prefix}{number:07d}" for number in range(first, last)]
            ran = node.run(["touch", "--", *names], cwd=params.path)
            if ran.exit_code != 0:
                error = full(ran)
                if error is None:
                    raise Refusal("command_failed", program="touch", status=ran.exit_code)
                stopped = error
                break
        total, used = inodes(node, posixpath.dirname(params.path))
        return {"inodes_total": total, "inodes_used": used, "stopped_by": stopped}


class UsageParams(Params):
    """A level of the root disk's use to compare with."""

    percent: Percent
    """The share of the root disk's configured size."""
    below: bool = True
    """Whether the use must be below that share (true) or at it or above (false)."""


class DiskUsageBelow(Probe[UsageParams]):
    """``disk_usage_below``: whether a node's root disk is in use below a percentage."""

    name = "disk_usage_below"
    version = 1
    Params = UsageParams
    reports = frozenset({"size_bytes", "usage_bytes"})

    def describe(self, params: UsageParams) -> str:
        """One sentence that says what the probe asks with these parameters."""
        side = "below" if params.below else "at or above"
        return (
            f"Passes while the use of the root disk of the node {params.node} is {side}"
            f" {params.percent} % of its configured size."
        )

    def _measure(self, targets: Targets, params: UsageParams) -> Verdict:
        size, used = measured(targets.node(params.node))
        is_below = used * 100 < size * params.percent
        return Verdict(
            "passed" if is_below == params.below else "failed",
            None,
            {"size_bytes": size, "usage_bytes": used},
        )


class InodeUsageParams(Params):
    """A level of a file system's inodes in use to compare with."""

    path: AbsolutePath | Literal["/"] = "/"
    """A path on the file system that is asked about."""
    percent: Percent
    """The share of the file system's inodes."""
    below: bool = True
    """Whether the inodes in use must be below that share (true) or at it or above (false)."""


class InodeUsageBelow(Probe[InodeUsageParams]):
    """``inode_usage_below``: whether a file system's inodes are in use below a percentage."""

    name = "inode_usage_below"
    version = 1
    Params = InodeUsageParams
    requires = Requires((STAT,))
    reports = frozenset({"inodes_total", "inodes_used"})

    def describe(self, params: InodeUsageParams) -> str:
        """One sentence that says what the probe asks with these parameters."""
        side = "below" if params.below else "at or above"
        return (
            f"Passes while the inodes in use on the file system of {params.path} on the node"
            f" {params.node} are {side} {params.percent} % of all it has."
        )

    def _measure(self, targets: Targets, params: InodeUsageParams) -> Verdict:
        total, used = inodes(targets.node(params.node), params.path)
        is_below = used * 100 < total * params.percent
        return Verdict(
            "passed" if is_below == params.below else "failed",
            None,
            {"inodes_total": total, "inodes_used": used},
        )


PRIMITIVES: Final = (DiskFill, InodeExhaust, PgFillDisk)
PROBES: Final = (DiskUsageBelow, InodeUsageBelow)
