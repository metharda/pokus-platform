"""``scenario.yaml``: one version of a scenario template.

The scenario names its blueprint, its split and its parameters, the probes
and fault primitives of its phases, and the ticket the model is shown. It
holds nothing of the ground truth, which is a file of its own. A scenario
keeps its ``id`` across versions: the ID is the template's identity.

The ticket and the steps' parameters may use the scenario's parameters as
templates (`potka_core.catalog.parameters`); a template that names a
parameter the scenario does not declare is refused, so every instance of a
valid scenario resolves.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import Field, NonNegativeInt, model_validator

from potka_core.catalog.base import Count, Identifier, Model, Name, ScenarioId, Text
from potka_core.catalog.parameters import (
    Parameter,
    Scalar,
    Template,
    derive_all,
    placeholders,
    render_text,
)
from potka_core.catalog.steps import Probe, Step

Split = Literal["train", "dev", "test"]
RiskLevel = Literal["low", "medium", "high"]
Isolation = Literal["container", "vm"]
MAX_SETTLE_SECONDS = 3600


class Budgets(Model):
    """The agent's budgets a scenario replaces; the others stay the agent configuration's."""

    max_steps: Count | None = None
    """The most steps of the agent."""
    max_tool_calls: Count | None = None
    """The most tool calls."""
    max_tokens_total: Count | None = None
    """The most tokens, input and output together."""
    max_wall_s: Count | None = None
    """The longest session, in seconds."""

    @model_validator(mode="after")
    def _replaces_one(self) -> Budgets:
        if all(getattr(self, name) is None for name in type(self).model_fields):
            raise ValueError("budgets_override replaces no budget")
        return self


class Scenario(Model):
    """One version of a scenario template (scenario.yaml)."""

    id: ScenarioId
    """The scenario's ID, the same in every version: the template's identity."""
    version: Count
    """This version of the scenario."""
    title: Text
    """What the scenario is, for people; no model sees it."""
    risk_level: RiskLevel
    """The scenario's risk level."""
    isolation: Isolation
    """The kind of instance every node of the scenario's blueprint runs in."""
    blueprint: Name
    """The name of the blueprint description the scenario runs on."""
    split: Split
    """The split the scenario belongs to, the same in every version."""
    tags: tuple[Name, ...]
    """Tags a campaign can select by, none of them twice."""
    parameters: dict[Identifier, Parameter]
    """The parameters by name; each instance derives their values from its seed."""
    preconditions: tuple[Probe, ...]
    """Probes that must pass before the fault."""
    setup: tuple[Step, ...]
    """Steps that prepare the target before the fault."""
    inject: tuple[Step, ...]
    """The fault primitives, in the order they are applied."""
    confirm: tuple[Probe, ...]
    """Probes that pass once the fault shows."""
    confirm_timeout_seconds: Count
    """How long the confirm probes have to pass: the confirming phase's timeout."""
    settle_seconds: Annotated[NonNegativeInt, Field(le=MAX_SETTLE_SECONDS)]
    """How long to wait after the fault is confirmed, for its symptoms to spread."""
    ticket: Template
    """The ticket the model is shown; it may use the parameters as ${name}."""
    budgets_override: Budgets | None = None
    """The agent's budgets this scenario replaces, if any."""

    @model_validator(mode="after")
    def _consistent(self) -> Scenario:
        twice = sorted({tag for tag in self.tags if self.tags.count(tag) > 1})
        if twice:
            raise ValueError(f"tag given twice: {', '.join(twice)}")
        unknown = sorted(set(self.placeholders()) - set(self.parameters))
        if unknown:
            raise ValueError(f"a template uses an undeclared parameter: {', '.join(unknown)}")
        return self

    def steps(self) -> tuple[Probe | Step, ...]:
        """Every step, phase by phase: preconditions, setup, inject, confirm."""
        return (*self.preconditions, *self.setup, *self.inject, *self.confirm)

    def placeholders(self) -> tuple[str, ...]:
        """The parameters the ticket and the steps use (a name as often as used)."""
        used = placeholders(self.ticket)
        for step in self.steps():
            used += step.placeholders()
        return used

    def values(self, seed: int) -> dict[str, Scalar]:
        """The parameters' values in the instance of the scenario seed SEED.

        Args:
            seed: The instance's scenario seed, a signed 64-bit integer.

        Returns:
            Each parameter's value by name.
        """
        return derive_all(seed, self.parameters)

    def render_ticket(self, values: Mapping[str, Scalar]) -> str:
        """The ticket of one instance.

        Args:
            values: The instance's parameter values by name (`values`).

        Returns:
            The ticket's text with every placeholder filled.
        """
        return render_text(self.ticket, values)
