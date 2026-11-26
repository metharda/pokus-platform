"""What systemd says of a unit, the faults that act on one, and the probe that asks.

One command says everything the library asks of a unit:
``systemctl show --property=LoadState,ActiveState,UnitFileState -- <unit>``,
three ``key=value`` lines that are read by key. ``is-active`` is never
asked: it says ``inactive`` of a unit that does not exist.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from typing import Annotated, Final, Literal

from potka_core.shells import is_shell
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
from potka_primitives.context import Node, Targets
from potka_primitives.errors import Refusal
from potka_primitives.values import AnyUnit, Unit

SYSTEMCTL: Final = Program("systemctl", "systemd")
MKDIR: Final = Program("mkdir", "coreutils")
PROPERTIES: Final = ("LoadState", "ActiveState", "UnitFileState")
# The words systemd has for the three properties; a unit without a file has no file state.
LOAD_STATES: Final = frozenset({"loaded", "not-found", "masked", "error", "bad-setting", "stub"})
ACTIVE_STATES: Final = frozenset(
    {"active", "reloading", "inactive", "failed", "activating", "deactivating", "maintenance"}
)
FILE_STATES: Final = frozenset(
    {
        "",
        "alias",
        "bad",
        "disabled",
        "enabled",
        "enabled-runtime",
        "generated",
        "indirect",
        "linked",
        "linked-runtime",
        "masked",
        "masked-runtime",
        "static",
        "transient",
    }
)
RUNNING: Final = frozenset({"active", "activating", "reloading"})
ENABLED: Final = frozenset({"enabled", "enabled-runtime"})
MASKED: Final = frozenset({"masked", "masked-runtime"})
_LINE: Final = re.compile(r"([A-Za-z]+)=([a-z-]*)")
DROP_INS: Final = "/etc/systemd/system"
_COMMAND: Final = re.compile(r"/[A-Za-z0-9/._=:@,+-]*( [A-Za-z0-9/._=:@,+-]+)*")


@dataclass(frozen=True)
class UnitState:
    """What systemd says of a unit.

    Attributes:
        load: Whether its file was found and read (``loaded``).
        active: Whether it runs (``active``, ``inactive``, ``failed``).
        file: Whether it is enabled (``enabled``, ``disabled``, ``masked``);
            empty for a unit that has no file.
    """

    load: str
    active: str
    file: str


def unit_state(node: Node, unit: str) -> UnitState:
    """The state of a unit, whether it exists or not.

    Raises:
        Refusal: ``command_failed``, or ``unexpected_output`` when the
            answer is not the three properties with words systemd has.
    """
    ran = node.run(["systemctl", "show", f"--property={','.join(PROPERTIES)}", "--", unit])
    lines = ran.expect(0).text().splitlines()
    said: dict[str, str] = {}
    for line in lines:
        matched = _LINE.fullmatch(line)
        if matched is None or matched[1] in said:
            raise Refusal("unexpected_output", program="systemctl", status=0)
        said[matched[1]] = matched[2]
    if (
        set(said) != set(PROPERTIES)
        or said["LoadState"] not in LOAD_STATES
        or said["ActiveState"] not in ACTIVE_STATES
        or said["UnitFileState"] not in FILE_STATES
    ):
        raise Refusal("unexpected_output", program="systemctl", status=0)
    return UnitState(said["LoadState"], said["ActiveState"], said["UnitFileState"])


def loaded(node: Node, unit: str) -> UnitState:
    """The state of a unit that systemd has loaded.

    Raises:
        Refusal: ``unit_not_found`` for any other.
    """
    state = unit_state(node, unit)
    if state.load != "loaded":
        raise Refusal("unit_not_found")
    return state


def after(node: Node, unit: str) -> Facts:
    """A unit's state as facts: two of systemd's own words."""
    state = unit_state(node, unit)
    return {"active_state": state.active, "unit_file_state": state.file or "none"}


WORDS: Final = frozenset({*ACTIVE_STATES, *FILE_STATES, "none"} - {""})


class ActionParams(Params):
    """A unit and what is done to it."""

    unit: Unit
    """The unit."""
    action: Literal["start", "stop", "restart", "disable", "mask"]
    """What systemd is asked to do with it."""


class SystemdAction(Primitive[ActionParams]):
    """``systemd.action``: start, stop, restart, disable or mask a unit."""

    name = "systemd.action"
    version = 1
    Params = ActionParams
    requires = Requires((SYSTEMCTL,))
    reports = frozenset({"active_state", "unit_file_state"})
    words = WORDS

    def describe(self, params: ActionParams) -> str:
        """One sentence that says what the step does with these parameters."""
        return f"Asks systemd on the node {params.node} to {params.action} {params.unit}."

    def side_effects(self, params: ActionParams) -> Effects:
        """The unit it acts on, and what systemd keeps of a mask or of a unit no longer enabled."""
        if params.action == "mask":
            return Effects(files=(f"{DROP_INS}/{params.unit}",), units=(params.unit,))
        if params.action == "disable":
            return Effects(units=(params.unit,), other=("the links that enable the unit",))
        return Effects(units=(params.unit,))

    def _ready(self, node: Node, params: ActionParams) -> None:
        if params.action == "mask":
            state = unit_state(node, params.unit)
            if state.load == "masked" or state.file in MASKED:
                raise Refusal("already_in_that_state")
            if state.load != "loaded":
                raise Refusal("unit_not_found")
            return
        state = loaded(node, params.unit)
        in_place = {
            "start": state.active in RUNNING,
            "stop": state.active not in RUNNING,
            "restart": False,
            "disable": state.file not in ENABLED,
        }
        if in_place[params.action]:
            raise Refusal("already_in_that_state")

    def _apply(self, node: Node, params: ActionParams) -> Facts:
        ran = node.run(["systemctl", params.action, "--", params.unit], wait=180)
        # A unit that does not come up is the fault a start may be asked for: systemd says
        # so with the status 1, and the state afterwards is the fact.
        ran.expect(0, 1) if params.action in ("start", "restart") else ran.expect(0)
        state = unit_state(node, params.unit)
        # Of the other three, the state afterwards must be the fault's: systemd can end
        # one with the status 0 and leave the unit as it was (enabled for this boot only).
        made = {
            "stop": state.active not in RUNNING,
            "disable": state.file not in ENABLED,
            "mask": state.load == "masked" or state.file in MASKED,
        }
        if not made.get(params.action, True):
            raise Refusal("command_failed", program="systemctl", status=ran.exit_code)
        return {"active_state": state.active, "unit_file_state": state.file or "none"}


def _service(unit: str) -> str:
    if not unit.endswith(".service"):
        raise ValueError("the unit is a service")
    return unit


def _command(line: str) -> str:
    if _COMMAND.fullmatch(line) is None:
        raise ValueError(
            "a command is one line of words of letters, digits and /._=:@,+- with one blank"
            " between them, and its first word is an absolute path"
        )
    program = line.split(" ", 1)[0]
    if posixpath.normpath(program) != program or program == "/":
        raise ValueError("a command's program is an absolute path in its normal form")
    if any(is_shell(word) for word in line.split(" ")):
        raise ValueError("a command is a program and its arguments, never a shell's")
    return line


class BreakParams(Params):
    """A service and the command it is given in place of its own."""

    unit: Annotated[Unit, AfterValidator(_service)]
    """The service."""
    exec_start: Annotated[str, Field(max_length=1000), AfterValidator(_command)]
    """The command line: a program that is not in the target, and its arguments."""

    @property
    def program(self) -> str:
        """The command's program."""
        return self.exec_start.split(" ", 1)[0]

    @property
    def directory(self) -> str:
        """The directory of the unit's drop-in files."""
        return f"{DROP_INS}/{self.unit}.d"

    @property
    def drop_in(self) -> str:
        """The drop-in file the fault writes, where ``systemctl edit`` writes its own."""
        return f"{self.directory}/override.conf"


class SystemdBreakUnit(Primitive[BreakParams]):
    """``systemd.break_unit``: give a service a command that cannot be run.

    The command replaces the service's own in a drop-in file. Nothing is
    restarted: the service fails at its next start.
    """

    name = "systemd.break_unit"
    version = 1
    Params = BreakParams
    requires = Requires((SYSTEMCTL, MKDIR))

    def describe(self, params: BreakParams) -> str:
        """One sentence that says what the step does with these parameters."""
        return (
            f"Gives {params.unit} on the node {params.node} the start command"
            f" {params.exec_start}, whose program is not there, in a drop-in file."
        )

    def side_effects(self, params: BreakParams) -> Effects:
        """The drop-in directory and file, and the unit."""
        return Effects(files=(params.directory, params.drop_in), units=(params.unit,))

    def _ready(self, node: Node, params: BreakParams) -> None:
        loaded(node, params.unit)
        if node.exists(params.drop_in):
            raise Refusal("file_exists")
        if node.exists(params.program):
            raise Refusal("program_exists")

    def _apply(self, node: Node, params: BreakParams) -> Facts:
        node.run(["mkdir", "--parents", "--", params.directory]).expect(0)
        content = f"[Service]\nExecStart=\nExecStart={params.exec_start}\n"
        node.write(params.drop_in, content.encode(), mode=0o644)
        node.run(["systemctl", "daemon-reload"], wait=180).expect(0)
        return {}


class StateParams(Params):
    """A unit and the state it is asked to be in."""

    unit: AnyUnit
    """The unit, by the form of its name alone: the probe only reads, so the units a fault
    may not act on, systemd's own and the lab's channel into a target, may be asked about."""
    state: Literal[
        "active", "reloading", "inactive", "failed", "activating", "deactivating", "maintenance"
    ]
    """The state systemd must say the unit is in."""


class UnitInState(Probe[StateParams]):
    """``unit_state``: whether a unit that systemd has loaded is in a state.

    A unit that systemd has not loaded, one it does not know or one that
    is masked, is in no state that can be asked for: the probe fails for
    every state, and its facts say what systemd said.
    """

    name = "unit_state"
    version = 1
    Params = StateParams
    requires = Requires((SYSTEMCTL,))
    reports = frozenset({"load_state", "active_state"})
    words = frozenset({*LOAD_STATES, *ACTIVE_STATES})

    def describe(self, params: StateParams) -> str:
        """One sentence that says what the probe asks with these parameters."""
        return (
            f"Passes while systemd on the node {params.node} has {params.unit} loaded and says"
            f" that it is {params.state}."
        )

    def _measure(self, targets: Targets, params: StateParams) -> Verdict:
        state = unit_state(targets.node(params.node), params.unit)
        as_asked = state.load == "loaded" and state.active == params.state
        return Verdict(
            "passed" if as_asked else "failed",
            None,
            {"load_state": state.load, "active_state": state.active},
        )


PRIMITIVES: Final = (SystemdAction, SystemdBreakUnit)
PROBES: Final = (UnitInState,)
