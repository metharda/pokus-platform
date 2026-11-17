"""``blueprints/<name>.yaml``: the system a scenario runs on, as scenarios and tools see it.

A blueprint description lists the nodes of the system under a neutral
alias each: the golden image a node is launched from (the infrastructure's
blueprint and its version, never a fingerprint: golden images are built on
each host), the derived image built on it when the node needs one, its
services, its background components with the settings a fault may change,
the paths a tool may read and patch, and the endpoints probes reach. It is
not ground truth: the executor's view of a run's targets comes from it.

It also names the golden image of the probe client, from which probes
reach the nodes' endpoints. The probe client is not a node: every run gets
one beside its nodes, and no tool of the agent lists or targets it.

A node's background components come with its derived image, whose overlay
is the directory ``blueprints/<derived name>/overlay/``
(`potka_core.catalog.overlay`). A component's settings file can be read
and patched; whatever can be patched can be read, and no patchable path
lies inside another, so every file has one entry and one validator.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from potka_core.catalog.base import Count, Model, Name, Text, plain
from potka_core.catalog.parameters import Scalar
from potka_core.catalog.steps import Probe


def _path(value: str) -> str:
    parts = value.split("/")
    if (
        len(parts) < 2
        or parts[0] != ""
        or any(part in ("", ".", "..") for part in parts[1:])
        or not value.isprintable()
    ):
        raise ValueError("not an absolute path in its normal form")
    return value


AbsolutePath = Annotated[str, Field(pattern=r"^(/[^/]+)+$"), AfterValidator(_path)]
"""An absolute path of printable characters, without ``.``, ``..``, doubled or
trailing slashes; not ``/`` itself."""
Alias = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]{0,14}$")]
"""A node's neutral alias, the only name of the node a model sees."""
Unit = Annotated[str, Field(pattern=r"^[a-z0-9@._-]+\.service$", max_length=255)]
"""A systemd service unit."""
Setting = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$", max_length=63)]
"""The name of a setting in a component's settings file."""


def within(path: str, roots: tuple[str, ...]) -> bool:
    """Whether PATH is one of ROOTS or lies below one.

    Args:
        path: An absolute path in its normal form.
        roots: Files and directories, each an absolute path in its normal form.

    Returns:
        True when a root is PATH or a directory above it.
    """
    return any(path == root or path.startswith(root + "/") for root in roots)


def _twice(what: str, values: tuple[str, ...]) -> None:
    twice = sorted({value for value in values if values.count(value) > 1})
    if twice:
        raise ValueError(f"{what} given twice: {', '.join(twice)}")


class Golden(Model):
    """A golden image, by the infrastructure's blueprint and its version."""

    blueprint: Text
    """The infrastructure's blueprint."""
    version: Text
    """The blueprint's version."""


class Derived(Model):
    """A derived image: the platform builds it on the node's golden image from an overlay."""

    name: Name
    """Its name; the overlay is the directory blueprints/<name>/overlay/."""
    version: Count
    """Its version; a changed overlay is a new version."""


class Component(Model):
    """A background component: a job of the application that runs in every run."""

    name: Name
    """The component's name."""
    settings_file: AbsolutePath
    """The file its settings are in, which the component reads at every start."""
    settings: Annotated[
        dict[Setting, Annotated[Scalar, AfterValidator(plain)]], Field(min_length=1)
    ]
    """The settings a fault may change, each with its normal value."""


class Patchable(Model):
    """A path a tool may patch, and the command that checks the result."""

    path: AbsolutePath
    """A file, or a directory for every file below it."""
    validator: Annotated[tuple[Text, ...], Field(min_length=1)] | None
    """The command and its arguments, run after a patch; null for no check."""


class Endpoint(Model):
    """An endpoint of a node that probes reach from the probe client."""

    name: Name
    """The endpoint's name."""
    scheme: Literal["http", "https", "tcp"]
    """How the endpoint is spoken to."""
    port: Annotated[int, Field(ge=1, le=65535)]
    """Its TCP port."""
    path: Annotated[str, Field(pattern=r"^/[!-~]*$")] | None
    """The path of an http or https endpoint; null for tcp."""

    @model_validator(mode="after")
    def _path_of_http(self) -> Endpoint:
        if (self.path is None) != (self.scheme == "tcp"):
            raise ValueError("an http or https endpoint has a path, and a tcp endpoint has none")
        return self


class Node(Model):
    """One node of the system."""

    alias: Alias
    """The node's neutral alias: the only name of the node a model sees."""
    golden: Golden
    """The golden image the node, or its derived image, is built on."""
    derived: Derived | None = None
    """The derived image the node is launched from, if it needs one."""
    services: tuple[Unit, ...]
    """The systemd services of the application, which tools may act on."""
    components: tuple[Component, ...]
    """The background components; they come with the derived image."""
    allowed_read_paths: tuple[AbsolutePath, ...]
    """The files, and the directories with everything below them, a tool may read."""
    patchable_paths: tuple[Patchable, ...]
    """The paths a tool may patch, all of them readable."""
    endpoints: tuple[Endpoint, ...]
    """The endpoints probes reach."""

    @model_validator(mode="after")
    def _consistent(self) -> Node:
        _twice("service", self.services)
        _twice("component", tuple(component.name for component in self.components))
        _twice("endpoint", tuple(endpoint.name for endpoint in self.endpoints))
        _twice("allowed read path", self.allowed_read_paths)
        patchable = tuple(entry.path for entry in self.patchable_paths)
        _twice("patchable path", patchable)
        hidden = sorted(path for path in patchable if not within(path, self.allowed_read_paths))
        if hidden:
            raise ValueError(f"patchable but not readable: {', '.join(hidden)}")
        # One entry, and so one validator, for every file that can be patched.
        inside = sorted(
            f"{path} in {other}"
            for path in patchable
            for other in patchable
            if path != other and within(path, (other,))
        )
        if inside:
            raise ValueError(f"patchable paths inside each other: {', '.join(inside)}")
        if self.components and self.derived is None:
            raise ValueError("background components need a derived image")
        for component in self.components:
            if not within(component.settings_file, patchable):
                raise ValueError(f"the settings file of {component.name} is not patchable")
        return self

    def component(self, name: str) -> Component | None:
        """The background component NAME, or None.

        Args:
            name: The component's name.

        Returns:
            The component, or None when the node has none of that name.
        """
        return next((c for c in self.components if c.name == name), None)


class Blueprint(Model):
    """A blueprint description (blueprints/<name>.yaml)."""

    name: Name
    """The description's name, which scenarios refer to; the file is <name>.yaml."""
    version: Count
    """The description's version."""
    probe_client: Golden
    """The golden image of the probe client, which every run gets and which is no node."""
    nodes: Annotated[tuple[Node, ...], Field(min_length=1)]
    """The nodes of the system."""
    health: tuple[Probe, ...]
    """Probes that pass while the system is healthy; they use no scenario parameter."""

    @model_validator(mode="after")
    def _consistent(self) -> Blueprint:
        _twice("node alias", tuple(node.alias for node in self.nodes))
        if any(probe.placeholders() for probe in self.health):
            raise ValueError("a health probe uses a scenario parameter")
        return self

    def node(self, alias: str) -> Node | None:
        """The node ALIAS, or None.

        Args:
            alias: The node's alias.

        Returns:
            The node, or None when the blueprint has none of that alias.
        """
        return next((node for node in self.nodes if node.alias == alias), None)
