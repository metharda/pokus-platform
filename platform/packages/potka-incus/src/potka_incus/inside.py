"""The inside of an in-memory instance: its files, its root disk, its commands.

A test's stand-in for what lives in a target. Nothing here runs a program:
a command is answered from what the test scripted for exactly that list of
arguments (or by a function the test gave), and a command nobody scripted
fails the test.

The files are a tree of directories, regular files and symbolic links with
owners and modes. A path is looked up as Linux looks one up: every symbolic
link on the way is followed, and the last part too unless the call is one
that acts on a link itself; ``.`` and ``..`` are steps; a part that is
missing is ``ENOENT``, a file where a directory must be is ``ENOTDIR``, and
links that lead in a circle are ``ELOOP``.

The root disk has the size the instance was given and counts, in bytes
and not in blocks, what the files hold and what was allocated for them. A
write to a full disk keeps
what still fits and then fails, as a real one does once it has truncated
the file. The error is the storage pool driver's: ``ENOSPC`` where the
instance has a disk of its own (``lvm``), ``EDQUOT`` where its limit is a
quota (``btrfs``).
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["file", "directory", "symlink"]
# A full root disk's error by the storage pool's driver, and the root file system that an
# instance of such a pool reports unless the test says otherwise.
FULL = {"lvm": "ENOSPC", "btrfs": "EDQUOT"}
FILESYSTEMS = {"lvm": "ext4", "btrfs": "btrfs"}
MESSAGES = {
    "EBUSY": "device or resource busy",
    "EINVAL": "invalid argument",
    "EDQUOT": "disk quota exceeded",
    "EEXIST": "file exists",
    "EISDIR": "is a directory",
    "ELOOP": "too many levels of symbolic links",
    "ENOENT": "no such file or directory",
    "ENOSPC": "no space left on device",
    "ENOTDIR": "not a directory",
    "ENOTEMPTY": "directory not empty",
}
MAX_LINKS = 40


class Problem(Exception):
    """A file operation the instance refuses.

    Attributes:
        error: The error's name (``ENOENT``, ``ENOSPC``).
    """

    def __init__(self, error: str, path: str) -> None:
        """Keep the error's name; the text is the path and the error in words."""
        super().__init__(f"{path}: {MESSAGES[error]}")
        self.error = error


@dataclass
class Node:
    """A file, a directory or a symbolic link.

    Attributes:
        kind: What it is.
        content: A file's bytes, or a link's target.
        uid: Its owner.
        gid: Its group.
        mode: Its permission bits.
        allocated: The bytes allocated for a file from its start; its content lies in
            them, so the file takes the larger of the two.
    """

    kind: Kind
    content: bytes = b""
    uid: int = 0
    gid: int = 0
    mode: int = 0o644
    allocated: int = 0


@dataclass(frozen=True)
class Execution:
    """A command the instance was asked to run.

    Attributes:
        argv: The program and its arguments.
        environment: The variables given with it.
        cwd: The directory given with it, or None.
        user: The user ID it runs as.
        group: The group ID it runs as.
    """

    argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...] = ()
    cwd: str | None = None
    user: int = 0
    group: int = 0


@dataclass(frozen=True)
class Ended:
    """How a scripted command ends.

    Attributes:
        exit_code: Its exit status.
        stdout: Its standard output.
        stderr: Its standard error.
    """

    exit_code: int = 0
    stdout: bytes = b""
    stderr: bytes = b""


Answer = Callable[["Inside", Execution], Ended | None]


@dataclass
class Inside:
    """What is inside one instance.

    Attributes:
        size: The root disk's configured size in bytes, or None without one.
        driver: The storage pool's driver, ``lvm`` or ``btrfs``: it decides
            the error of a full disk.
        filesystem: The root file system's type; the driver's usual one
            (``ext4`` on ``lvm``) when not given.
        base: The bytes the image itself uses.
        nodes: Every file, directory and link by its absolute path.
        executions: Every command asked for, in order.
    """

    size: int | None = None
    driver: str = "lvm"
    filesystem: str = ""
    base: int = 0
    nodes: dict[str, Node] = field(default_factory=lambda: {"/": Node("directory", mode=0o755)})
    executions: list[Execution] = field(default_factory=list)
    _answers: list[Answer] = field(default_factory=list, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Refuse a driver the stand-in does not know, where the mistake is made."""
        if self.driver not in FULL:
            raise ValueError(f"not a storage pool driver of the stand-in: {self.driver!r}")
        self.filesystem = self.filesystem or FILESYSTEMS[self.driver]

    @property
    def usage(self) -> int:
        """The bytes of the root disk in use: the image's, and each file's content or allocation.

        A file takes the larger of what it holds and what was allocated for
        it. Bytes are counted, not blocks: a directory, a link and an empty
        file take nothing.
        """
        files = (n for n in self.nodes.values() if n.kind == "file")
        return self.base + sum(max(len(n.content), n.allocated) for n in files)

    @staticmethod
    def _parts(text: str) -> list[str]:
        """The steps of a path or of a link's target; a final slash is a last step ``""``."""
        parts = [part for part in text.split("/") if part]
        return [*parts, ""] if parts and text.endswith("/") else parts

    def _find(self, path: str, follow: bool) -> tuple[str, Node | None, bool]:
        """Where PATH leads: its real path, what is there, and whether a directory is asked for.

        Every symbolic link before the last part is followed, and the last
        part too when FOLLOW or when a slash or a ``.`` comes after it.
        Everything before the last part must exist. The node is None when
        only the last part is missing; the third value then says whether
        the path (or the link that led there) named it with a final slash.

        Raises:
            Problem: ``ENOENT``, ``ENOTDIR`` or ``ELOOP`` on the way.
            ValueError: PATH is not absolute: a test's mistake.
        """
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError(f"not an absolute path: {path!r}")
        todo = self._parts(path)
        done, followed = "/", 0
        while todo:
            part = todo.pop(0)
            if part in ("", "."):
                continue
            if part == "..":
                done = posixpath.dirname(done)
                continue
            here = posixpath.join(done, part)
            node = self.nodes.get(here)
            if node is None:
                # Only a final slash may come after a part that is missing.
                if any(todo):
                    raise Problem("ENOENT", path)
                return here, None, bool(todo)
            if node.kind == "symlink" and (todo or follow):
                followed += 1
                target = node.content.decode()
                if followed > MAX_LINKS:
                    raise Problem("ELOOP", path)
                if not target:
                    raise Problem("ENOENT", path)
                todo = self._parts(target) + todo
                done = "/" if target.startswith("/") else done
                continue
            if todo and node.kind != "directory":
                raise Problem("ENOTDIR", path)
            done = here
        return done, self.nodes[done], False

    def realpath(self, path: str) -> str:
        """PATH with every symbolic link followed, as ``realpath -e`` prints it.

        Raises:
            Problem: A part of the path does not exist (``ENOENT``), is no
                directory where one must be (``ENOTDIR``), or links form a
                loop (``ELOOP``).
        """
        done, found, _ = self._find(path, True)
        if found is None:
            raise Problem("ENOENT", path)
        return done

    def lookup(self, path: str) -> tuple[str, Node]:
        """What PATH names, a link itself when its last part is one; with its real path.

        As ``lstat``: a final slash or ``.`` follows a last link.

        Raises:
            Problem: As :meth:`realpath`.
        """
        done, found, _ = self._find(path, False)
        if found is None:
            raise Problem("ENOENT", path)
        return done, found

    def _children(self, directory: str) -> tuple[str, ...]:
        prefix = directory.rstrip("/") + "/"
        names = (p[len(prefix) :] for p in self.nodes if p.startswith(prefix) and p != prefix)
        return tuple(sorted(name for name in names if "/" not in name))

    def mkdir(self, path: str, *, uid: int = 0, gid: int = 0, mode: int = 0o755) -> None:
        """Make a directory and the directories above it that are missing (``mkdir -p``).

        A link to a directory is gone through; nothing is made behind a
        link that leads nowhere.

        Raises:
            Problem: A part of the path is a file (``ENOTDIR``, or
                ``EEXIST`` for the last part) or a link that leads nowhere
                (``EEXIST``), or links form a loop (``ELOOP``).
            ValueError: PATH is not absolute.
        """
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError(f"not an absolute path: {path!r}")
        parts = [part for part in path.split("/") if part]
        for count in range(1, len(parts) + 1):
            last = count == len(parts)
            prefix = "/" + "/".join(parts[:count])
            done, found, _ = self._find(prefix, True)
            if found is None:
                # Something is there all the same when the name is a link to nothing.
                if self._find(prefix, False)[1] is not None:
                    raise Problem("EEXIST", path)
                owner = (uid, gid, mode) if last else (0, 0, 0o755)
                self.nodes[done] = Node("directory", b"", *owner)
            elif found.kind != "directory":
                raise Problem("EEXIST" if last else "ENOTDIR", path)

    def link(self, path: str, target: str) -> None:
        """Make a symbolic link at PATH that names TARGET.

        Raises:
            Problem: Something is at PATH already (``EEXIST``); its
                directory is missing, PATH ends in a slash or TARGET is
                empty (``ENOENT``); a part on the way is no directory
                (``ENOTDIR``) or links form a loop (``ELOOP``).
        """
        done, found, slash = self._find(path, False)
        if found is not None:
            raise Problem("EEXIST", path)
        if slash or not target:
            raise Problem("ENOENT", path)
        self.nodes[done] = Node("symlink", target.encode(), mode=0o777)

    def write(
        self,
        path: str,
        content: bytes,
        *,
        uid: int = 0,
        gid: int = 0,
        mode: int = 0o644,
        append: bool = False,
    ) -> None:
        """Write a regular file as Incus's files API does.

        Links are followed, the last part too: a write through a link whose
        target is missing makes the target. A new file gets the owner and
        mode given; an existing one keeps its own. An append needs the file
        to exist. Without APPEND the file is emptied first.

        Raises:
            Problem: A directory on the way is missing (``ENOENT``) or is
                none (``ENOTDIR``), links form a loop (``ELOOP``), the file
                of an append is missing (``ENOENT``), PATH is a directory
                or names a missing file with a final slash (``EISDIR``), or
                the root disk is full (``ENOSPC`` or ``EDQUOT``): then the
                file holds what still fitted, as it would after a real write.
        """
        done, found, slash = self._find(path, True)
        if (found is not None and found.kind == "directory") or (found is None and slash):
            raise Problem("EISDIR", path)
        if found is None and append:
            raise Problem("ENOENT", path)
        node = found or Node("file")
        self.nodes[done] = node
        if not append:
            node.content, node.allocated = b"", 0
        if self.size is not None:
            # What was allocated for the file and is not written yet is its own to fill.
            own = max(node.allocated - len(node.content), 0)
            room = max(self.size - self.usage, 0) + own
            if len(content) > room:
                node.content += content[:room]
                raise Problem(FULL[self.driver], path)
        node.content += content
        if found is None:
            node.uid, node.gid, node.mode = uid, gid, mode

    def allocate(self, path: str, length: int) -> str | None:
        """Allocate the first LENGTH bytes of the file PATH, as ``fallocate --length`` does.

        The file is made when it is missing, and it stays, empty, when the
        disk is full: the program makes it before it asks for the bytes.
        What is allocated or written already is not taken again, so a
        second call of the same length takes nothing. Deleting the file
        gives the bytes back. The content is not made longer: a read still
        gives what was written.

        Returns:
            None when the bytes are the file's; else the error of a full
            disk (``ENOSPC`` or ``EDQUOT``), with nothing taken.

        Raises:
            Problem: PATH is a directory or names a missing file with a
                final slash (``EISDIR``), or a part on the way is missing
                or no directory.
            ValueError: LENGTH is not a whole number above 0.
        """
        if type(length) is not int or length <= 0:
            raise ValueError(f"not a number of bytes to allocate: {length!r}")
        done, found, slash = self._find(path, True)
        if (found is not None and found.kind != "file") or (found is None and slash):
            raise Problem("EISDIR", path)
        node = found or Node("file")
        self.nodes[done] = node
        more = max(length - max(len(node.content), node.allocated), 0)
        if self.size is not None and self.usage + more > self.size:
            return FULL[self.driver]
        node.allocated = max(node.allocated, length)
        return None

    def delete(self, path: str) -> None:
        """Remove a file, a link itself or an empty directory.

        A last link is never followed: with a final slash it is no
        directory to remove.

        Raises:
            Problem: PATH does not exist (``ENOENT``), is a directory that
                is not empty (``ENOTEMPTY``), is ``/`` (``EBUSY``), ends in
                ``.`` (``EINVAL``), or ends in a slash and is no directory
                (``ENOTDIR``).
        """
        bare = path.rstrip("/") if isinstance(path, str) else path
        if isinstance(bare, str) and posixpath.basename(bare) == ".":
            raise Problem("EINVAL", path)
        done, found, _ = self._find(bare or "/", False)
        if found is None:
            raise Problem("ENOENT", path)
        if done == "/":
            raise Problem("EBUSY", path)
        if path.endswith("/") and found.kind != "directory":
            raise Problem("ENOTDIR", path)
        if found.kind == "directory" and self._children(done):
            raise Problem("ENOTEMPTY", path)
        del self.nodes[done]

    def listing(self, path: str) -> tuple[str, ...]:
        """The names directly in the directory PATH, sorted.

        Raises:
            Problem: PATH does not exist (``ENOENT``) or is no directory
                (``ENOTDIR``).
        """
        done = self.realpath(path)
        if self.nodes[done].kind != "directory":
            raise Problem("ENOTDIR", path)
        return self._children(done)

    def on(
        self,
        argv: Sequence[str],
        exit_code: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
    ) -> None:
        """Script the end of exactly the command ARGV, each time it is asked for.

        Only the arguments are compared; the environment, the directory and
        the user of a command are kept in :attr:`executions` for the test
        to assert.

        Raises:
            TypeError: ARGV is no list or tuple of strings.
        """
        if not isinstance(argv, (list, tuple)) or not all(isinstance(a, str) for a in argv):
            raise TypeError("a scripted command is a list of strings")
        expected, ended = tuple(argv), Ended(exit_code, stdout, stderr)
        self._answers.append(lambda inside, asked: ended if asked.argv == expected else None)

    def answer(self, function: Answer) -> None:
        """Let FUNCTION end the commands it knows (it returns None for any other).

        FUNCTION gets this inside and may change it, as a command would (a
        scripted ``fallocate`` allocates). Every function given later is
        asked before it.
        """
        self._answers.append(function)

    def run(self, execution: Execution) -> Ended:
        """Record a command and end it as scripted; the last script that knows it wins.

        Raises:
            AssertionError: Nothing scripted this command: the test fails.
        """
        self.executions.append(execution)
        for answer in reversed(self._answers):
            ended = answer(self, execution)
            if ended is not None:
                return ended
        raise AssertionError(f"no answer scripted for the command {list(execution.argv)}")
