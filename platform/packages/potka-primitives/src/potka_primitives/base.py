"""What a fault primitive and a probe are.

A fault primitive has a name, a version, a model of its parameters, the
programs it uses in a target, and three calls its caller makes in this
order:

* `Primitive.check` for every step of a scenario, before anything is
  changed for any of them: every program the primitive uses is on the
  node, and the node is one the fault can be made on at all;
* `Primitive.ready` immediately before a step is applied: the node is in
  the state the fault starts from, and the fault is not already in place;
* `Primitive.apply`: the fault.

The three are this module's and a primitive does not replace them; it
gives what they call (`Primitive._suited`, `Primitive._ready`,
`Primitive._apply`). `check` and `ready` read and never write. A fault
that fails half made is not undone: its run is invalid and its targets are
deleted.

A probe measures and changes nothing. Its verdict is ``passed``,
``failed`` or ``error``; an error is neither of the other two.

A step's parameters arrive resolved, as a mapping; `Step.parameters`
makes the model of them. What a primitive returns (`Facts`) is a measured
number or a word of the primitive's own closed list, never a text of its
parameters or of a program's output.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from potka_primitives.context import Node, Targets
from potka_primitives.errors import REASONS, FaultFailed, PrimitiveError, Refusal, SetupRefused
from potka_primitives.values import Alias

# The packages of the operating system image itself, which every golden image is built
# from and which no table of what a blueprint adds can list.
BASE_IMAGE: Final = frozenset({"coreutils", "systemd", "util-linux"})
_NAME: Final = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?")
_PROGRAM: Final = re.compile(r"[a-z][a-z0-9_.+-]*")

Facts = Mapping[str, int | str]


@dataclass(frozen=True)
class Program:
    """A program a primitive or a probe runs in a target.

    Attributes:
        name: Its name, without a directory.
        package: The package that brings it into a target: the one that
            ships it, or a service's own package where a package that one
            depends on ships the service's client program.
    """

    name: str
    package: str


@dataclass(frozen=True)
class Requires:
    """What a primitive or a probe uses in a target.

    Attributes:
        programs: Every program it runs. One whose package is `service`
            is the faulted service's own tool; every other is of a package
            that every golden image carries.
        service: The package of the service it acts on, which the image of
            the node must carry; None when it acts on no service.
    """

    programs: tuple[Program, ...] = ()
    service: str | None = None


@dataclass(frozen=True)
class Effects:
    """What a fault changes in a target.

    Attributes:
        files: The files and directories it writes, makes or changes.
        units: The units it acts on.
        other: What else it changes that is neither, each in a few words
            (a network interface's queueing discipline, a container).
    """

    files: tuple[str, ...] = ()
    units: tuple[str, ...] = ()
    other: tuple[str, ...] = ()


@dataclass(frozen=True)
class Verdict:
    """What a probe found.

    Attributes:
        outcome: ``passed``, ``failed``, or ``error`` when it could not measure.
        reason: Why it could not measure, a word of the library's list;
            None for the other outcomes.
        facts: What it measured.
    """

    outcome: Literal["passed", "failed", "error"]
    reason: str | None = None
    facts: Facts = field(default_factory=dict)


def _under[T](kind: type[PrimitiveError], node: Node, call: Callable[[], T]) -> T:
    """What CALL returns; a refusal raised below it becomes an error of KIND.

    The error is raised after the refusal is done with, and carries its
    reason, its program and its status with the node's alias.
    """
    refused: Refusal
    try:
        return call()
    except Refusal as refusal:
        refused = refusal
    raise kind(refused.reason, alias=node.alias, program=refused.program, status=refused.status)


class Strict(BaseModel):
    """A model of a step's parameters or of a part of them.

    Unknown keys are refused, nothing is converted, and it is frozen. An
    error of such a model never shows the value that was given.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


class Params(Strict):
    """The base of every parameter model: a step acts on one node."""

    node: Alias
    """The alias of the node the step acts on."""


class Step[P: Params]:
    """What a fault primitive and a probe have in common.

    Attributes:
        name: Its name in a scenario's steps.
        version: A number that rises when what it does changes.
        Params: The model of its parameters.
        requires: What it uses in a target.
        reports: The names of the facts it may give.
        words: The words its facts may hold; every other fact is a number.
    """

    name: ClassVar[str]
    version: ClassVar[int]
    Params: ClassVar[type[Any]]
    requires: ClassVar[Requires] = Requires()
    reports: ClassVar[frozenset[str]] = frozenset()
    words: ClassVar[frozenset[str]] = frozenset()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Refuse a class that is not what the library's index can hold.

        Raises:
            TypeError: A class with a name lacks its version or its
                parameter model, has a name or a program of another form,
                or would answer one of the base classes' own calls with
                something else, whoever in its bases defines it.
        """
        super().__init_subclass__(**kwargs)
        for base, calls in _SEALED:
            # The kind of step it is, by name: the two classes are defined below.
            kind = next((one for one in cls.__mro__[1:] if one.__name__ == base), None)
            if kind is None or kind.__module__ != __name__:
                continue
            replaced = [
                call
                for call in calls
                if inspect.getattr_static(cls, call) is not inspect.getattr_static(kind, call)
            ]
            if replaced:
                raise TypeError(f"{cls.__name__} replaces {', '.join(replaced)}")
        if "name" not in cls.__dict__:
            return
        if _NAME.fullmatch(cls.name) is None:
            raise TypeError(f"{cls.__name__}: not a step's name")
        if type(cls.__dict__.get("version")) is not int or cls.version < 1:
            raise TypeError(f"{cls.__name__}: no version")
        model = cls.__dict__.get("Params")
        if not (isinstance(model, type) and issubclass(model, Params)):
            raise TypeError(f"{cls.__name__}: no parameter model")
        for program in cls.requires.programs:
            if _PROGRAM.fullmatch(program.name) is None:
                raise TypeError(f"{cls.__name__}: a program is a name without a directory")

    @classmethod
    def parameters(cls, given: Mapping[str, JsonValue]) -> P:
        """The model of a step's resolved parameters.

        They are read as the JSON they come from, so a list is a sequence
        and nothing else is converted.

        Raises:
            pydantic.ValidationError: They do not fit; no value is shown.
            TypeError: A key is no string, or a value is none that JSON has.
            ValueError: A number is none that JSON has (not a number, infinite).
        """
        if not all(type(key) is str for key in given):
            raise TypeError("a parameter's name is a string")
        made: P = cls.Params.model_validate_json(json.dumps(dict(given), allow_nan=False))
        return made

    def describe(self, params: P) -> str:
        """One sentence that says what the step does with these parameters."""
        raise NotImplementedError

    def _facts(self, facts: Facts) -> Facts:
        """FACTS, when each has a name the class reports and is a number or one of its words.

        Raises:
            TypeError: A fact is something else: a name or a text the class
                does not give could be a parameter's or a program's. The
                error says no more than that, not even whose fact it is.
        """
        for name, value in facts.items():
            named = type(name) is str and name in self.reports
            if not named or not (
                type(value) is int or (type(value) is str and value in self.words)
            ):
                raise TypeError("a fact that the step does not report, or no number and no word")
        return dict(facts)


# The calls that are the base classes' own, by the class that has them.
_SEALED: Final = (
    ("Primitive", ("check", "ready", "apply", "parameters", "_facts")),
    ("Probe", ("run", "parameters", "_facts")),
)


class Primitive[P: Params](Step[P]):
    """A fault primitive."""

    def side_effects(self, params: P) -> Effects:
        """What applying the step with these parameters changes in the target."""
        raise NotImplementedError

    def _suited(self, node: Node, params: P) -> None:
        """Refuse a node the fault cannot be made on, whatever earlier steps do. Reads only."""

    def _ready(self, node: Node, params: P) -> None:
        """Refuse a node that is not in the state the fault starts from. Reads only."""

    def _apply(self, node: Node, params: P) -> Facts:
        """Make the fault and say what was reached."""
        raise NotImplementedError

    def check(self, targets: Targets, params: P) -> None:
        """Whether the step's node has what the primitive uses and can take the fault.

        For every step of a scenario, before the first is applied.

        Raises:
            SetupRefused: A program is not on the node (``tool_missing``),
                or the node is not one the fault can be made on.
            TargetFailure: Incus did not do what was asked.
            UnknownNode: The run has no node of the step's alias.
        """
        node = targets.node(params.node)

        def suited() -> None:
            for program in self.requires.programs:
                if not node.has(program.name):
                    raise Refusal("tool_missing", program=program.name)
            self._suited(node, params)

        _under(SetupRefused, node, suited)

    def ready(self, targets: Targets, params: P) -> None:
        """Whether the node is in the state the fault starts from; just before `apply`.

        Raises:
            SetupRefused: It is not, or the fault is in place already.
            TargetFailure: Incus did not do what was asked.
            UnknownNode: The run has no node of the step's alias.
        """
        node = targets.node(params.node)
        _under(SetupRefused, node, lambda: self._ready(node, params))

    def apply(self, targets: Targets, params: P) -> Facts:
        """Make the fault.

        Returns:
            What was reached: numbers that were measured and words of the
            primitive's own list.

        Raises:
            FaultFailed: It could not be made; what was changed stays.
            TargetFailure: Incus did not do what was asked.
            UnknownNode: The run has no node of the step's alias.
        """
        node = targets.node(params.node)
        return _under(FaultFailed, node, lambda: self._facts(self._apply(node, params)))


class Probe[P: Params](Step[P]):
    """A probe.

    A refusal that a probe lets pass is an ``error``: the probe could not
    measure. Where a refusal means that what the probe asks is not so (the
    file it should find is gone), the probe catches it and says ``failed``.
    """

    def _measure(self, targets: Targets, params: P) -> Verdict:
        """Measure, and say whether what was measured is as the parameters ask."""
        raise NotImplementedError

    def run(self, targets: Targets, params: P) -> Verdict:
        """Measure once.

        Returns:
            ``passed`` or ``failed``, or ``error`` with a reason when the
            probe could not measure.

        Raises:
            TargetFailure: Incus did not do what was asked.
            UnknownNode: The run has no node of the step's alias.
            TypeError: The probe gave a verdict that is none: an outcome
                of another word, or a reason without an error.
        """
        try:
            verdict = self._measure(targets, params)
        except Refusal as refusal:
            return Verdict("error", refusal.reason)
        measured = verdict.outcome in ("passed", "failed") and verdict.reason is None
        if not measured and not (verdict.outcome == "error" and verdict.reason in REASONS):
            raise TypeError("a verdict that is none")
        return Verdict(verdict.outcome, verdict.reason, self._facts(verdict.facts))
