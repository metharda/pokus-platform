"""The steps of a scenario and of its ground truth: fault primitives and probes.

A step names a primitive of the injector or a probe of the verifier and
gives its parameters, whose strings may use the scenario's parameters
(`potka_core.catalog.parameters`). The models here check a step's form
only; whether the name exists, and what its parameters must be, is the
libraries' matter.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated

from pydantic import Field, JsonValue

from potka_core.catalog.base import Identifier, Model
from potka_core.catalog.parameters import Params, Scalar, render, tree_placeholders

PrimitiveName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$", max_length=63)]
"""A fault primitive's name: its family and its action, joined by a dot."""


class _Step(Model):
    params: Params
    """Its parameters; their strings may use the scenario's parameters as ${name}."""

    def placeholders(self) -> tuple[str, ...]:
        """The scenario parameters the step's parameters use (a name as often as used)."""
        return tree_placeholders(self.params)

    def render(self, values: Mapping[str, Scalar]) -> dict[str, JsonValue]:
        """The step's parameters in one instance.

        Args:
            values: The instance's parameter values by name.

        Returns:
            The parameters with every placeholder filled.

        Raises:
            TemplateError: A placeholder names a parameter VALUES lacks.
        """
        return {key: render(item, values) for key, item in self.params.items()}


class Step(_Step):
    """A fault primitive to apply, with its parameters."""

    primitive: PrimitiveName
    """The fault primitive's name."""


class Probe(_Step):
    """A probe to run, with its parameters."""

    probe: Identifier
    """The probe's name."""
