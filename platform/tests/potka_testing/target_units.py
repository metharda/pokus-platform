"""The units and the file programs of a target, answered for the tests.

``systemctl`` as the fault primitives call it, from a table of units that a
test fills; ``stat``, ``chmod``, ``chown`` and ``mkdir --parents`` from the
stand-in's own files. Only these forms are answered: a command in another
form is nobody's and fails its test. The lines made here have the form of
the kept outputs (`potka_testing.outputs`), which a test holds them to.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from potka_incus.inside import Ended, Execution, Inside, Problem

from potka_testing.outputs import output

SHOW = ("systemctl", "show", "--property=LoadState,ActiveState,UnitFileState", "--")
KINDS = {"directory": "directory", "symlink": "symbolic link"}


@dataclass
class Units:
    """A node's units, as systemd would say them.

    Attributes:
        within: The node's inside.
        units: Each unit's load state, active state and file state.
        broken: The units that fail when they are started.
        asked: Every action asked of systemd, in order.
    """

    within: Inside
    units: dict[str, list[str]] = field(default_factory=dict)
    broken: set[str] = field(default_factory=set)
    asked: list[tuple[str, ...]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Let this object end the node's systemctl commands."""
        self.within.answer(self._answer)

    def add(self, unit: str, active: str = "active", file: str = "enabled") -> None:
        """A unit that systemd has loaded."""
        self.units[unit] = ["loaded", active, file]

    def _answer(self, within: Inside, asked: Execution) -> Ended | None:
        match asked.argv:
            case (*show, unit) if tuple(show) == SHOW:
                load, active, file = self.units.get(unit, ["not-found", "inactive", ""])
                said = f"LoadState={load}\nActiveState={active}\nUnitFileState={file}\n"
                return Ended(0, said.encode())
            case ("systemctl", "daemon-reload"):
                self.asked.append(("daemon-reload",))
                return Ended(0)
            case ("systemctl", action, "--", *units) if action in (
                "start",
                "stop",
                "restart",
                "reload",
                "disable",
                "mask",
            ):
                self.asked.append((action, *units))
                for unit in units:
                    state = self.units.get(unit)
                    if state is None or state[0] != "loaded":
                        return Ended(5, b"", b"Failed: Unit not found.\n")
                    if action == "stop":
                        state[1] = "inactive"
                    elif action in ("start", "restart"):
                        state[1] = "failed" if unit in self.broken else "active"
                        if unit in self.broken:
                            return output("systemctl-restart-failed").ended()
                    elif action == "disable":
                        state[2] = "disabled"
                    elif action == "mask":
                        state[0], state[2] = "masked", "masked"
                    elif state[1] != "active":
                        return Ended(1, b"", b"Job failed.\n")
                return Ended(0)
        return None


@dataclass
class Files:
    """A node's ``stat``, ``chmod``, ``chown`` and ``mkdir --parents``."""

    within: Inside

    def __post_init__(self) -> None:
        """Let this object end those commands."""
        self.within.answer(self._answer)

    def _answer(self, within: Inside, asked: Execution) -> Ended | None:
        try:
            match asked.argv:
                case ("stat", "--format=%a:%u:%g:%F", "--", path):
                    _, node = within.lookup(path)
                    empty = "regular file" if node.content else "regular empty file"
                    kind = KINDS.get(node.kind, empty)
                    said = f"{node.mode:o}:{node.uid}:{node.gid}:{kind}\n"
                    return Ended(0, said.encode())
                case ("chmod", "--", mode, path):
                    within.nodes[within.realpath(path)].mode = int(mode, 8)
                    return Ended(0)
                case ("chown", "--no-dereference", "--", spec, path):
                    _, node = within.lookup(path)
                    uid, _, gid = spec.partition(":")
                    node.uid = int(uid) if uid else node.uid
                    node.gid = int(gid) if gid else node.gid
                    return Ended(0)
                case ("mkdir", "--parents", "--", path):
                    within.mkdir(path)
                    return Ended(0)
        except Problem:
            return Ended(1, b"", b"cannot do that: No such file or directory\n")
        return None
