"""The disk programs of a target, answered from the stand-in's own state.

``findmnt``, ``fallocate``, ``stat --file-system``, ``mkdir`` and ``touch``
as the fault primitives call them, and no other form of them: a command in
another form is nobody's and fails its test. What a program prints when a
disk is full is a kept output (`potka_testing.outputs`); the two lines this
module makes itself and the library reads, a mount and an inode count, have
the form of the kept ones, which a test holds them to. What a failed
``mkdir`` says here is this module's own: the library reads its status alone.

The stand-in has no inodes of its own. Here every file, directory and link
of its tree takes one, and the image takes `Disk.taken` more.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field

from potka_incus.inside import FULL, Ended, Execution, Inside, Problem

from potka_testing.outputs import output


@dataclass
class Disk:
    """A node's disk programs.

    Attributes:
        within: The node's inside.
        inodes: The inodes its root file system has.
        taken: The inodes the image itself uses.
        mounts: Directories that lie on another file system, each with its mount point.
    """

    within: Inside
    inodes: int = 524_288
    taken: int = 100_000
    mounts: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Let this object end the node's disk commands."""
        self.within.answer(self._answer)

    @property
    def used(self) -> int:
        """The inodes in use."""
        return self.taken + len(self.within.nodes)

    def _answer(self, within: Inside, asked: Execution) -> Ended | None:
        full = FULL[within.driver].lower()
        match asked.argv:
            case ("findmnt", "--noheadings", "--output", "TARGET", target) if target.startswith(
                "--target="
            ):
                mount = self.mounts.get(target.removeprefix("--target="), "/")
                return Ended(0, f"{mount}\n".encode())
            case ("fallocate", length, "--", path) if length.startswith("--length="):
                error = within.allocate(path, int(length.removeprefix("--length=")))
                return Ended(0) if error is None else output(f"fallocate-{full}").ended()
            case ("stat", "--file-system", "--format=%c:%d:%T", "--", path):
                within.realpath(path)
                if within.filesystem == "btrfs":
                    return output("stat-fs-btrfs").ended()
                return Ended(0, f"{self.inodes}:{self.inodes - self.used}:ext2/ext3\n".encode())
            case ("mkdir", "--", path):
                try:
                    within.realpath(posixpath.dirname(path))
                    if path in within.nodes:
                        return Ended(1, b"", b"mkdir: cannot create directory: File exists\n")
                    within.mkdir(path)
                except Problem:
                    return Ended(1, b"", b"mkdir: cannot create directory\n")
                return Ended(0)
            case ("touch", "--", *names) if asked.cwd is not None:
                for name in names:
                    if within.filesystem != "btrfs" and self.used >= self.inodes:
                        return output(f"touch-{full}").ended()
                    within.write(posixpath.join(asked.cwd, name), b"")
                return Ended(0)
        return None
