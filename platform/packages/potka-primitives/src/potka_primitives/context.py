"""How a fault primitive or a probe reaches a run's targets.

A `Targets` holds a run's nodes by their neutral aliases; a `Node` is one
of them. Everything goes through the Incus client: a command as a list of
arguments, the file calls, an instance's record and state. Nothing here
creates, starts, stops or deletes an instance.

Every command runs with the same environment, set here: the command path
(`PATH`) and the locale ``C``, so that neither a profile's settings nor a
target's locale decides which program runs or how its errors read.

No error of the client leaves a `Node`. The client's messages name the
instance and the path asked for; here each becomes an error of the library
(`potka_primitives.errors`), raised on its own, with no cause attached.
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from potka_incus import client as incus

from potka_primitives.errors import Refusal, TargetFailure, UnknownNode

# The directories a command's program is looked for in, in order: Incus's own default.
PATH: Final = ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin")
ENVIRONMENT: Final[Mapping[str, str]] = {"PATH": ":".join(PATH), "LC_ALL": "C"}
# The seconds a command may take unless its caller says otherwise.
WAIT: Final = 60
# The statuses with which Incus ends a program that is not there or cannot be run.
NOT_RUN: Final = (126, 127)

Kind = Literal["file", "directory", "symlink"]
_FAILURES: Final[tuple[tuple[type[incus.IncusError], str], ...]] = (
    (incus.WrongKind, "wrong_kind"),
    (incus.NotFound, "not_found"),
    (incus.Forbidden, "forbidden"),
    (incus.Unreachable, "unreachable"),
    (incus.OperationTimeout, "operation_timeout"),
    (incus.OperationFailed, "operation_failed"),
    (incus.OutputLost, "output_lost"),
)


def _failure(error: Exception) -> str:
    """The kind of a client's error, as a reason of `TargetFailure`."""
    if not isinstance(error, incus.IncusError):
        # The client refused an argument before it sent anything.
        return "not_sent"
    return next((kind for known, kind in _FAILURES if isinstance(error, known)), "incus_error")


@dataclass(frozen=True)
class Ran:
    """How a command ended.

    Attributes:
        program: The program that ran.
        exit_code: Its exit status.
        stdout: What it wrote to its standard output.
        stderr: What it wrote to its standard error.
    """

    program: str
    exit_code: int
    stdout: bytes
    stderr: bytes

    def expect(self, *statuses: int) -> Ran:
        """This result, when its status is one of STATUSES (0 when none is given).

        Raises:
            Refusal: ``command_failed``, with the program and its status.
        """
        if self.exit_code not in (statuses or (0,)):
            raise Refusal("command_failed", program=self.program, status=self.exit_code)
        return self

    def text(self) -> str:
        """The standard output as text.

        Raises:
            Refusal: ``unexpected_output`` when it is not UTF-8.
        """
        try:
            return self.stdout.decode("utf-8")
        except UnicodeDecodeError:
            pass
        raise Refusal("unexpected_output", program=self.program, status=self.exit_code)

    def said(self, words: str) -> bool:
        """Whether the standard error holds WORDS (read as bytes: an error's text may be cut)."""
        return words.encode() in self.stderr


class Node:
    """One node of a run, under its alias."""

    def __init__(self, client: incus.Incus, alias: str, instance: str) -> None:
        """Keep the client, the alias and the instance's name; nothing is asked yet."""
        self._client = client
        self._alias = alias
        self._instance = instance
        self._programs: frozenset[str] | None = None

    @property
    def alias(self) -> str:
        """The node's alias, the only name of it the library gives out."""
        return self._alias

    def _present(self) -> None:
        """Make sure the instance itself is there.

        Incus answers a call for a path of an instance that is gone as it
        answers one for a path that is not there. Before "not there" is
        said of a path, the instance is asked for.

        Raises:
            TargetFailure: The instance is not there (``not_found``), or
                Incus did not say.
        """
        self._ask(lambda: self._client.instance(self._instance))

    def _ask[T](self, call: Callable[[], T], **refusals: str) -> T:
        """What CALL returns; a client's error becomes one of the library's.

        REFUSALS says which kinds of failure are a refusal here, and with
        which reason (``not_found="file_not_found"``); every other kind is a
        `TargetFailure`. The error is raised after the client's is done
        with, so that it carries nothing of it.
        """
        kind: str
        try:
            return call()
        except (incus.IncusError, TypeError, ValueError) as error:
            kind = _failure(error)
        if kind in refusals:
            if kind == "not_found":
                self._present()
            raise Refusal(refusals[kind], alias=self._alias)
        raise TargetFailure(kind, alias=self._alias)

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        user: int = 0,
        group: int = 0,
        wait: int = WAIT,
    ) -> Ran:
        """Run a command in the node and wait for its end.

        Args:
            argv: The program, a name without a directory, and its
                arguments; no shell reads them.
            cwd: The directory to run it in.
            user: The user ID to run it as.
            group: The group ID to run it as.
            wait: The seconds to wait for its end.

        Returns:
            How it ended; a status other than 0 is a result.

        Raises:
            TypeError: ARGV is one string, or holds what is no string.
            ValueError: The program is given with a directory.
            Refusal: ``tool_missing``: the program is not there or cannot
                be run.
            TargetFailure: Incus did not run it or did not say how it ended.
        """
        if isinstance(argv, str) or not all(isinstance(word, str) for word in argv):
            raise TypeError("a command is a list of strings, never a command line")
        program = argv[0] if argv else ""
        if not program or "/" in program:
            raise ValueError("a command's program is a name without a directory")
        ended = self._ask(
            lambda: self._client.exec(
                self._instance,
                list(argv),
                environment=ENVIRONMENT,
                cwd=cwd,
                user=user,
                group=group,
                wait=wait,
            )
        )
        if ended.exit_code in NOT_RUN:
            raise Refusal(
                "tool_missing", alias=self._alias, program=program, status=ended.exit_code
            )
        return Ran(program, ended.exit_code, ended.stdout, ended.stderr)

    def read(self, path: str) -> incus.File:
        """A regular file's content, owner, group and mode.

        Raises:
            Refusal: ``file_not_found``, or ``not_a_regular_file`` for a
                directory or a symbolic link (a link is not followed, and
                one that leads nowhere is a link all the same).
            TargetFailure: Incus did not give it.
        """
        reason = ""
        try:
            return self._ask(
                lambda: self._client.read_file(self._instance, path),
                not_found="file_not_found",
                wrong_kind="not_a_regular_file",
            )
        except Refusal as refusal:
            reason = refusal.reason
        if reason == "file_not_found" and self.exists(path):
            # Its directory lists the name: something is there that cannot be reached.
            reason = "not_a_regular_file"
        raise Refusal(reason, alias=self._alias)

    def write(
        self, path: str, content: bytes, *, uid: int = 0, gid: int = 0, mode: int = 0o644
    ) -> None:
        """Write a regular file whole; one that exists keeps its owner and mode.

        Raises:
            Refusal: ``file_not_found``: the file's directory is not there.
            TargetFailure: Incus did not write it (a full disk among the reasons).
        """
        self._ask(
            lambda: self._client.write_file(
                self._instance, path, content, uid=uid, gid=gid, mode=mode
            ),
            not_found="file_not_found",
        )

    def remove(self, path: str) -> None:
        """Remove a file, a symbolic link itself or an empty directory.

        Raises:
            Refusal: ``file_not_found``.
            TargetFailure: Incus did not remove it.
        """
        self._ask(
            lambda: self._client.delete_file(self._instance, path), not_found="file_not_found"
        )

    def _listed(self, path: str) -> tuple[str, ...] | Kind | None:
        """A directory's names; else what PATH is, or None when nothing is there."""
        found = ""
        try:
            return self._client.list_directory(self._instance, path)
        except incus.NotFound:
            found = "nothing"
        except incus.WrongKind as wrong:
            found = wrong.found
        except (incus.IncusError, TypeError, ValueError) as error:
            found = "!" + _failure(error)
        if found == "nothing":
            self._present()
            return None
        if found == "file":
            return "file"
        if found == "symlink":
            return "symlink"
        raise TargetFailure(found[1:] if found.startswith("!") else "wrong_kind", alias=self._alias)

    def kind(self, path: str) -> Kind | None:
        """What PATH names (a symbolic link is not followed), or None for nothing.

        A name that its directory lists and that cannot be reached is a
        link that leads nowhere: something is there, and a write to it
        would land elsewhere.

        Raises:
            TargetFailure: Incus did not say.
        """
        listed = self._listed(path)
        if listed is None and path != "/":
            above = self._listed(posixpath.dirname(path))
            if isinstance(above, tuple) and posixpath.basename(path) in above:
                return "symlink"
        return "directory" if isinstance(listed, tuple) else listed

    def exists(self, path: str) -> bool:
        """Whether anything is at PATH, a link that leads nowhere too.

        Read from the listing of the directory above, so that nothing of a
        file's content is fetched. Where that directory is itself a link,
        the path is asked for, and a link in it that leads nowhere is not
        seen: who writes at a path makes sure first that its directory is
        one (`kind`).

        Raises:
            TargetFailure: Incus did not say.
        """
        if path == "/":
            return True
        above = self._listed(posixpath.dirname(path))
        if isinstance(above, tuple):
            return posixpath.basename(path) in above
        return above == "symlink" and self._listed(path) is not None

    def has(self, program: str) -> bool:
        """Whether a program of that name is in one of the command path's directories.

        A directory of the path that is not there, or that is a symbolic
        link (its target is in the path too), holds nothing here. The
        directories are listed once for the node; no process is started.

        Raises:
            TargetFailure: A directory could not be listed for another reason.
        """
        if self._programs is None:
            names: set[str] = set()
            for directory in PATH:
                listed = self._listed(directory)
                if isinstance(listed, tuple):
                    names.update(listed)
                elif listed == "file":
                    raise TargetFailure("wrong_kind", alias=self._alias)
            self._programs = frozenset(names)
        return program in self._programs

    def is_container(self) -> bool:
        """Whether the instance is a container (and no virtual machine).

        Raises:
            TargetFailure: Incus did not give the instance's record.
        """
        return self._ask(lambda: self._client.instance(self._instance)).kind == "container"

    def root_size(self) -> int | None:
        """The configured size of the root disk in bytes; None without one.

        A size in a form that cannot be read is none either: nothing is
        guessed.

        Raises:
            TargetFailure: Incus did not give the instance's record.
        """
        record = self._ask(lambda: self._client.instance(self._instance))
        try:
            return record.root_size
        except ValueError:
            return None

    def root_usage(self) -> int | None:
        """The bytes of the root disk in use, by the instance's state; None when it says none.

        Raises:
            TargetFailure: Incus did not give the record or the state.
        """
        device = self._ask(lambda: self._client.instance(self._instance)).root_device
        if device is None:
            return None
        used = self._ask(lambda: self._client.state(self._instance)).disk.get(device)
        return None if used is None else used.usage


class Targets:
    """A run's nodes, by alias."""

    def __init__(
        self, client: incus.Incus, nodes: Mapping[str, str], probe_client: str | None = None
    ) -> None:
        """Take the client and the instance's name of each alias.

        Args:
            client: The Incus client of the run's project.
            nodes: The instance's name of each node, by the node's alias.
            probe_client: The name of the run's probe client instance, if
                the caller needs it.
        """
        self._client = client
        self._nodes = {alias: Node(client, alias, name) for alias, name in nodes.items()}
        self._probe_client = (
            None if probe_client is None else Node(client, "probe-client", probe_client)
        )

    def node(self, alias: str) -> Node:
        """The node of that alias.

        Raises:
            UnknownNode: The run has none.
        """
        found = self._nodes.get(alias)
        if found is None:
            raise UnknownNode(alias)
        return found

    def probe_client(self) -> Node:
        """The run's probe client.

        Raises:
            UnknownNode: The caller gave none.
        """
        if self._probe_client is None:
            raise UnknownNode("probe-client")
        return self._probe_client
