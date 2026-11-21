"""The faults that write a file, edit one, or change who may read it.

A file is read and written through the files calls. They keep the owner
and the mode of a file that exists, so those are set with ``chown`` and
``chmod`` where a fault asks for them.

What these faults keep a scenario from, so that it places no program of
its own by accident: no file is made with an execute bit and none gains
one; a file that has one is not rewritten; and no file is written in the
places whose files are run, or name commands that are run, without
anything else being asked (`COMMAND_PLACES`: systemd's units, cron's
tables, what a login or a boot reads). The list is of the places that are
known, and no proof: that a file elsewhere is read by nothing that runs
what it says is the scenario's to hold.

A path that is a pipe or a device outside ``/dev`` is read like a file,
and the read ends only with the client's wait.
"""

from __future__ import annotations

import posixpath
import re
from typing import Annotated, Final, Literal

from potka_incus.client import File
from pydantic import AfterValidator, Field, model_validator

from potka_primitives.base import (
    Effects,
    Facts,
    Params,
    Primitive,
    Program,
    Requires,
    Strict,
)
from potka_primitives.context import Node
from potka_primitives.errors import Refusal
from potka_primitives.values import AbsolutePath, below

CHMOD: Final = Program("chmod", "coreutils")
CHOWN: Final = Program("chown", "coreutils")
STAT: Final = Program("stat", "coreutils")
EXECUTE: Final = 0o111
# What ``stat --format=%F`` calls the two kinds of file a fault may change.
CHANGEABLE: Final = frozenset({"regular file", "regular empty file", "directory"})
# ``stat`` prints a mode without leading zeros: 0 for none, 44 for a file only others read.
_STAT: Final = re.compile(r"([0-7]{1,4}):([0-9]{1,10}):([0-9]{1,10}):([a-z ]{1,40})\n")
PERMISSIONS: Final = 0o777
MOST_EDITS: Final = 16
# Where a file is run, or names commands that are run, by itself.
COMMAND_PLACES: Final = (
    "/etc/cron.d",
    "/etc/cron.daily",
    "/etc/cron.hourly",
    "/etc/cron.monthly",
    "/etc/cron.weekly",
    "/etc/crontab",
    "/etc/init.d",
    "/etc/ld.so.preload",
    "/etc/logrotate.conf",
    "/etc/logrotate.d",
    "/etc/profile",
    "/etc/profile.d",
    "/etc/rc.local",
    "/etc/sudoers",
    "/etc/sudoers.d",
    "/etc/systemd",
    "/etc/udev",
    "/lib/systemd",
    "/run/systemd",
    "/usr/lib/systemd",
    "/var/spool/cron",
)


def _text(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        pass
    else:
        if "\0" not in value:
            return value
    raise ValueError("a content is text that has a UTF-8 form and no NUL")


def _line(value: str) -> str:
    if any(character in value for character in "\n\r\0") or _text(value) != value:
        raise ValueError("a line is text without a line's end")
    return value


def _no_command_place(path: str) -> str:
    if any(below(path, place) for place in COMMAND_PLACES):
        raise ValueError("a file that is run, or that names commands that are, is not written")
    return path


def lines_of(text: str) -> list[str]:
    """TEXT's lines, each with its end; a line ends at a line feed and nowhere else."""
    return re.findall(r"[^\n]*\n|[^\n]+", text)


def _no_execute(mode: str) -> str:
    if int(mode, 8) & EXECUTE:
        raise ValueError("a file a fault writes has no execute bit")
    return mode


Mode = Annotated[str, Field(pattern=r"^[0-7]{3}$")]
"""A file's nine permission bits as three octal digits."""
Owner = Annotated[int, Field(ge=0, le=2**31 - 1)]
"""A user's or a group's number."""
Line = Annotated[str, Field(max_length=4096), AfterValidator(_line)]
"""One line of a file, without its end."""


def owner(uid: int | None, gid: int | None) -> str:
    """How ``chown`` is told a user, a group or both: as numbers, each marked with a plus.

    Without the mark ``chown`` looks for a user or a group of that name first.
    """
    return f"{'' if uid is None else f'+{uid}'}{'' if gid is None else f':+{gid}'}"


def own(node: Node, path: str, mode: str | None, uid: int | None, gid: int | None) -> None:
    """Set the owner and then the mode that are given, of a path itself and never of a link's end.

    Raises:
        Refusal: ``command_failed``.
    """
    if uid is not None or gid is not None:
        node.run(["chown", "--no-dereference", "--", owner(uid, gid), path]).expect(0)
    if mode is not None:
        node.run(["chmod", "--", mode, path]).expect(0)


WritablePath = Annotated[AbsolutePath, AfterValidator(_no_command_place)]
"""A file a fault may write: an absolute path outside the places of `COMMAND_PLACES`."""


class WriteParams(Params):
    """A file and what it holds."""

    path: WritablePath
    """The file; its directory is there."""
    content: Annotated[str, Field(max_length=65536), AfterValidator(_text)]
    """What the file holds afterwards."""
    mode: Annotated[Mode, AfterValidator(_no_execute)] | None = None
    """Its permission bits, none of them an execute bit; a new file's are 644 unless given."""
    uid: Owner | None = None
    """Its owner; a new file's is 0 unless given."""
    gid: Owner | None = None
    """Its group; a new file's is 0 unless given."""


class FileWrite(Primitive[WriteParams]):
    """``file.write``: write a file, new or in place of what it held."""

    name = "file.write"
    version = 1
    Params = WriteParams
    requires = Requires((CHMOD, CHOWN))
    reports = frozenset({"bytes"})

    def describe(self, params: WriteParams) -> str:
        """One sentence that says what the step does with these parameters."""
        return f"Writes the file {params.path} on the node {params.node}."

    def side_effects(self, params: WriteParams) -> Effects:
        """The file."""
        return Effects(files=(params.path,))

    def _there(self, node: Node, params: WriteParams) -> File | None:
        """The file as it is, or None where nothing is; what a write may not touch is refused.

        Raises:
            Refusal: ``file_not_found`` for a directory that is not there,
                ``not_a_regular_file`` for a directory that is a link or a
                path that holds no regular file, ``file_is_executable``.
        """
        above = node.kind(posixpath.dirname(params.path))
        if above != "directory":
            # A directory that is a link is named by where it leads, never through the link.
            raise Refusal("not_a_regular_file" if above == "symlink" else "file_not_found")
        if not node.exists(params.path):
            return None
        there = node.read(params.path)
        if there.mode & EXECUTE:
            raise Refusal("file_is_executable")
        return there

    def _ready(self, node: Node, params: WriteParams) -> None:
        there = self._there(node, params)
        if (
            there is not None
            and there.content == params.content.encode()
            and params.mode in (None, f"{there.mode:03o}")
            and params.uid in (None, there.uid)
            and params.gid in (None, there.gid)
        ):
            raise Refusal("already_in_that_state")

    def _apply(self, node: Node, params: WriteParams) -> Facts:
        content = params.content.encode()
        if self._there(node, params) is None:
            mode = int(params.mode or "644", 8)
            node.write(params.path, content, uid=params.uid or 0, gid=params.gid or 0, mode=mode)
        else:
            node.write(params.path, content)
            own(node, params.path, params.mode, params.uid, params.gid)
        return {"bytes": len(content)}


class SetKey(Strict):
    """Give the one line ``key = value`` of a file another value."""

    edit: Literal["set_key"]
    key: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]
    """The key: what stands before the line's first ``=``, without blanks."""
    value: Line
    """What stands after the ``=`` afterwards."""


class ReplaceLine(Strict):
    """Put another line in place of the one line that equals a text."""

    edit: Literal["replace_line"]
    line: Line
    replacement: Line


class InsertAfter(Strict):
    """Put a new line after the one line that equals a text."""

    edit: Literal["insert_after"]
    line: Line
    text: Line


class DeleteLine(Strict):
    """Take away the one line that equals a text."""

    edit: Literal["delete_line"]
    line: Line


Edit = Annotated[SetKey | ReplaceLine | InsertAfter | DeleteLine, Field(discriminator="edit")]


def _ending(line: str) -> tuple[str, str]:
    """A line's text and its end: a line feed, with a carriage return before it or not."""
    for end in ("\r\n", "\n"):
        if line.endswith(end):
            return line[: -len(end)], end
    return line, ""


def _one(found: list[int]) -> int:
    if len(found) != 1:
        raise Refusal("anchor_not_found" if not found else "anchor_not_unique")
    return found[0]


def edited(text: str, edits: tuple[SetKey | ReplaceLine | InsertAfter | DeleteLine, ...]) -> str:
    """TEXT after EDITS, each made on what the one before it left.

    Raises:
        Refusal: ``anchor_not_found`` or ``anchor_not_unique``: the line an
            edit names is not there exactly once.
    """
    lines = lines_of(text)
    for edit in edits:
        parts = [_ending(line) for line in lines]
        if isinstance(edit, SetKey):
            at = _one(
                [
                    index
                    for index, (body, _) in enumerate(parts)
                    if "=" in body
                    and not body.lstrip().startswith(("#", ";"))
                    and body.split("=", 1)[0].strip() == edit.key
                ]
            )
            body, end = parts[at]
            start, _, old = body.partition("=")
            # The blanks that stood after the sign stay; where none stood, none is put.
            blanks = old[: len(old) - len(old.lstrip(" \t"))]
            lines[at] = f"{start}={blanks}{edit.value}{end}"
            continue
        at = _one([index for index, (body, _) in enumerate(parts) if body == edit.line])
        end = parts[at][1] or "\n"
        if isinstance(edit, ReplaceLine):
            lines[at] = edit.replacement + parts[at][1]
        elif isinstance(edit, InsertAfter):
            lines[at : at + 1] = [parts[at][0] + end, edit.text + end]
        else:
            del lines[at]
    return "".join(lines)


class MutateParams(Params):
    """A file and the edits made to it, in their order."""

    path: WritablePath
    """The file, which is there."""
    edits: Annotated[tuple[Edit, ...], Field(min_length=1, max_length=MOST_EDITS)]
    """The edits."""


def read_text(node: Node, path: str) -> str:
    """The content, as text, of a regular file that a fault may rewrite.

    Raises:
        Refusal: ``file_not_found``, ``not_a_regular_file``,
            ``file_is_executable`` for a file with an execute bit, or
            ``unexpected_output`` for a content that is no UTF-8 text.
    """
    there = node.read(path)
    if there.mode & EXECUTE:
        raise Refusal("file_is_executable")
    try:
        return there.content.decode("utf-8")
    except UnicodeDecodeError:
        pass
    raise Refusal("unexpected_output")


class FileMutate(Primitive[MutateParams]):
    """``file.mutate``: edit a text file by lines; the file keeps its owner and mode."""

    name = "file.mutate"
    version = 1
    Params = MutateParams
    reports = frozenset({"bytes", "edits"})

    def describe(self, params: MutateParams) -> str:
        """One sentence that says what the step does with these parameters."""
        kinds = ", ".join(edit.edit for edit in params.edits)
        return f"Edits the file {params.path} on the node {params.node} ({kinds})."

    def side_effects(self, params: MutateParams) -> Effects:
        """The file."""
        return Effects(files=(params.path,))

    def _result(self, node: Node, params: MutateParams) -> str:
        before = read_text(node, params.path)
        result = edited(before, params.edits)
        if result == before:
            raise Refusal("already_in_that_state")
        return result

    def _ready(self, node: Node, params: MutateParams) -> None:
        self._result(node, params)

    def _apply(self, node: Node, params: MutateParams) -> Facts:
        content = self._result(node, params).encode()
        node.write(params.path, content)
        return {"bytes": len(content), "edits": len(params.edits)}


class PermissionParams(Params):
    """A file or a directory and what its mode and owner become."""

    path: AbsolutePath
    """The file or the directory, which is there."""
    mode: Mode | None = None
    """Its permission bits afterwards; no execute bit that it has not."""
    uid: Owner | None = None
    """Its owner afterwards."""
    gid: Owner | None = None
    """Its group afterwards."""

    @model_validator(mode="after")
    def _something(self) -> PermissionParams:
        if self.mode is None and self.uid is None and self.gid is None:
            raise ValueError("one of mode, uid and gid is given")
        return self


class PermissionsBreak(Primitive[PermissionParams]):
    """``permissions.break``: change the mode or the owner of a file or a directory."""

    name = "permissions.break"
    version = 1
    Params = PermissionParams
    requires = Requires((STAT, CHMOD, CHOWN))

    def describe(self, params: PermissionParams) -> str:
        """One sentence that says what the step does with these parameters."""
        return f"Changes the mode or the owner of {params.path} on the node {params.node}."

    def side_effects(self, params: PermissionParams) -> Effects:
        """The file or the directory."""
        return Effects(files=(params.path,))

    def _ready(self, node: Node, params: PermissionParams) -> None:
        if not node.exists(params.path):
            raise Refusal("file_not_found")
        ran = node.run(["stat", "--format=%a:%u:%g:%F", "--", params.path]).expect(0)
        said = _STAT.fullmatch(ran.text())
        if said is None:
            raise Refusal("unexpected_output", program="stat", status=0)
        if said[4] not in CHANGEABLE:
            raise Refusal("not_a_regular_file")
        # The nine permission bits are what a mode of three digits speaks of.
        mode, uid, gid = int(said[1], 8) & PERMISSIONS, int(said[2]), int(said[3])
        asked = None if params.mode is None else int(params.mode, 8)
        if asked is not None and asked & EXECUTE & ~mode:
            raise Refusal("adds_execute_bit")
        if asked in (None, mode) and params.uid in (None, uid) and params.gid in (None, gid):
            raise Refusal("already_in_that_state")

    def _apply(self, node: Node, params: PermissionParams) -> Facts:
        own(node, params.path, params.mode, params.uid, params.gid)
        return {}


PRIMITIVES: Final = (FileWrite, FileMutate, PermissionsBreak)
