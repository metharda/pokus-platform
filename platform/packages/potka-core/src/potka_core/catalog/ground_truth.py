"""``ground_truth.yaml``: what is wrong in a scenario, and what counts as right.

The file lies next to its scenario and is read only by the services that
may know the answer. It names the fault, the labels a correct diagnosis
may carry, the actions that are acceptable, required after them or
harmful, the probes that verify the resolution and those that find
collateral damage, and the resolved state in words. A scenario without a
fault has the fault category ``none`` and neither component nor location.

Its strings may use the scenario's parameters as templates
(`potka_core.catalog.parameters`): the fault's location, and the
parameters of probes and actions. The file cannot know which parameters
its scenario declares, so only a template's form is checked here.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from potka_core.catalog.base import Identifier, Label, Model, Text
from potka_core.catalog.parameters import Params, Template, placeholders, tree_placeholders
from potka_core.catalog.steps import Probe
from potka_core.catalog.taxonomy import NONE


class Fault(Model):
    """The fault: its taxonomy label, the component it is in and where there."""

    category: Label
    """The fault's taxonomy label; none when nothing is wrong."""
    component: Text | None
    """The component the fault is in; null only without a fault."""
    location: Template | None
    """Where in the component the fault is; null only without a fault."""

    @model_validator(mode="after")
    def _none_has_no_place(self) -> Fault:
        absent = self.category == NONE
        if (self.component is None) != absent or (self.location is None) != absent:
            raise ValueError(
                f"a fault has a component and a location, and only the category {NONE} has neither"
            )
        return self


class Diagnosis(Model):
    """The labels a correct diagnosis may carry."""

    accepted_labels: Annotated[tuple[Label, ...], Field(min_length=1)]
    """The labels that count as correct, the fault's category among them."""

    @model_validator(mode="after")
    def _distinct(self) -> Diagnosis:
        if len(set(self.accepted_labels)) != len(self.accepted_labels):
            raise ValueError("accepted_labels lists a label twice")
        return self


class Action(Model):
    """A tool call: the tool, and the arguments a matching call has."""

    tool: Identifier
    """The tool's name."""
    args: Params
    """The arguments a matching call has; strings may use the parameters as ${name}."""

    def placeholders(self) -> tuple[str, ...]:
        """The scenario parameters the arguments use (a name as often as used)."""
        return tree_placeholders(self.args)


class Actions(Model):
    """The actions that count: acceptable ones, those required after them, harmful ones."""

    acceptable: tuple[Action, ...]
    """The actions that count as acceptable."""
    then_required: tuple[Action, ...]
    """Actions required after an acceptable one."""
    harmful: tuple[Action, ...]
    """The actions that count as harmful."""

    def all(self) -> tuple[Action, ...]:
        """Every action: acceptable, then required, harmful."""
        return (*self.acceptable, *self.then_required, *self.harmful)


class GroundTruth(Model):
    """A scenario's ground truth (ground_truth.yaml)."""

    fault: Fault
    """What is wrong."""
    diagnosis: Diagnosis
    """What a correct diagnosis says."""
    actions: Actions
    """The actions that count for or against the agent."""
    verification: tuple[Probe, ...]
    """Probes that pass once the fault is resolved."""
    collateral: tuple[Probe, ...]
    """Probes that fail when something else was damaged."""
    resolution_state: Text
    """The state the target is in once the fault is resolved, in words."""

    @model_validator(mode="after")
    def _fault_is_accepted(self) -> GroundTruth:
        if self.fault.category not in self.diagnosis.accepted_labels:
            raise ValueError("the fault's category is not among the accepted labels")
        return self

    def labels(self) -> tuple[str, ...]:
        """Every taxonomy label the file uses: the fault's category and the accepted labels."""
        return (self.fault.category, *self.diagnosis.accepted_labels)

    def placeholders(self) -> tuple[str, ...]:
        """The scenario parameters the file's templates use (a name as often as used)."""
        used = placeholders(self.fault.location or "")
        steps: tuple[Action | Probe, ...] = (
            *self.actions.all(),
            *self.verification,
            *self.collateral,
        )
        for step in steps:
            used += step.placeholders()
        return used
