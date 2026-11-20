"""A run's targets as a tool sees them: nodes, and what a call may name on each.

The context of a run lists its nodes under their neutral aliases, and for
each node its services, the paths a tool may read and patch (a patchable
path with the command that checks a patch's result), and its endpoints. A
call's arguments are checked against it (`potka_tools.arguments`).

It holds nothing of an instance (no name, no address) and nothing of a
scenario: only what a description of the system says. The service that
registers a run's targets fills it; here its form is checked, in the same
forms a description of the system has, and its content is not doubted. An
error of its form shows the place and never the input.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from potka_tools.template import SHELLS
from potka_tools.values import is_normal, printable


class Model(BaseModel):
    """The base of the context's models: unknown keys refused, nothing converted, frozen."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        hide_input_in_errors=True,
    )


def _path(value: str) -> str:
    if not is_normal(value) or not value.isprintable():
        raise ValueError("not an absolute path in its normal form")
    return value


def _word(value: str) -> str:
    if not printable(value):
        raise ValueError("not printable ASCII")
    return value


def _alias(value: str) -> str:
    # A probe is sent to the alias as a host's name: this one is the asking machine itself.
    if value == "localhost":
        raise ValueError("a node's alias is not the asking machine's own name")
    return value


def _command(words: tuple[str, ...] | None) -> tuple[str, ...] | None:
    # A validator runs in a node like a tool's command: a program and its arguments.
    if words is not None and any(word.rsplit("/", 1)[-1] in SHELLS for word in words):
        raise ValueError("a validator is a program and its arguments, never a shell's")
    return words


Alias = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]{0,14}$"), AfterValidator(_alias)]
"""A node's neutral alias, the only name of the node a model sees."""
Unit = Annotated[str, Field(pattern=r"^[a-z0-9@._-]+\.service$", max_length=255)]
"""A systemd service unit."""
AbsolutePath = Annotated[str, AfterValidator(_path)]
"""An absolute path of printable characters in its normal form; not ``/`` itself."""
Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$", max_length=63)]
"""An endpoint's name: lowercase words joined by hyphens."""


class Patchable(Model):
    """A path a tool may patch, and the command that checks the result."""

    path: AbsolutePath
    """A file, or a directory for every file below it."""
    validator: Annotated[
        Annotated[tuple[Annotated[str, AfterValidator(_word)], ...], Field(min_length=1)] | None,
        AfterValidator(_command),
    ]
    """The command and its arguments, run after a patch; None for no check. No shell."""


class Endpoint(Model):
    """An endpoint of a node, reached from the run's probe client."""

    name: Name
    """The endpoint's name."""
    scheme: Literal["http", "https", "tcp"]
    """How the endpoint is spoken to."""
    port: Annotated[int, Field(ge=1, le=65535)]
    """Its TCP port."""
    path: Annotated[str, Field(pattern=r"^/[!-~]*$")] | None
    """The path of an http or https endpoint; None for tcp."""

    @model_validator(mode="after")
    def _path_of_http(self) -> Endpoint:
        if (self.path is None) != (self.scheme == "tcp"):
            raise ValueError("an http or https endpoint has a path, and a tcp endpoint has none")
        return self


def _twice(what: str, values: tuple[str, ...]) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{what} given twice")


class NodeContext(Model):
    """One node of a run."""

    alias: Alias
    """The node's alias."""
    services: tuple[Unit, ...]
    """Its services."""
    readable_paths: tuple[AbsolutePath, ...]
    """The files and directories a tool may read."""
    patchable_paths: tuple[Patchable, ...]
    """The files and directories a tool may patch; each can be read too."""
    endpoints: tuple[Endpoint, ...]
    """Its endpoints."""

    @model_validator(mode="after")
    def _distinct(self) -> NodeContext:
        _twice("a service", self.services)
        _twice("a readable path", self.readable_paths)
        _twice("a patchable path", tuple(entry.path for entry in self.patchable_paths))
        _twice("an endpoint", tuple(endpoint.name for endpoint in self.endpoints))
        return self

    @property
    def readable(self) -> tuple[str, ...]:
        """Every path a tool may read: the readable ones and the patchable ones."""
        return (*self.readable_paths, *(entry.path for entry in self.patchable_paths))

    @property
    def patchable(self) -> tuple[str, ...]:
        """Every path a tool may patch."""
        return tuple(entry.path for entry in self.patchable_paths)

    def url(self, name: str) -> str | None:
        """What a probe is given for the http or https endpoint NAME, or None without one.

        The host is the node's alias, never an instance's name or address.
        """
        for endpoint in self.endpoints:
            if endpoint.name == name and endpoint.scheme != "tcp":
                return f"{endpoint.scheme}://{self.alias}:{endpoint.port}{endpoint.path}"
        return None


class Context(Model):
    """The nodes of a run."""

    nodes: tuple[NodeContext, ...]
    """The nodes, no alias twice."""

    @model_validator(mode="after")
    def _distinct(self) -> Context:
        _twice("an alias", tuple(node.alias for node in self.nodes))
        return self

    def node(self, alias: str) -> NodeContext | None:
        """The node of this alias, or None."""
        return next((node for node in self.nodes if node.alias == alias), None)
