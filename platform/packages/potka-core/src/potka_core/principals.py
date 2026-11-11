"""``principals.json``: the accounts, UIDs, sockets and socket clients infra rendered.

The model follows the file's JSON Schema in pokus-infrastructure's
``contract/`` at ``INFRA_PIN`` (contract version 2): every field. It adds
rules of its own, which infra's rendering keeps and which the allowlists
rely on: a socket's ``human_groups`` are the groups of its ``humans``, in
the same order (as the schema describes them); service names, accounts
and UIDs are unique across services and jobs; a service's sockets have
unique names, ``fd_name`` values and paths. Peer allowlists are built from
it (`potka_core.peer`), never from names written in code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

PATH = Path("/etc/pinka/platform/principals.json")
AGENT = "untrusted-agent"
Profile = Literal[
    "controller",
    "inference-broker",
    "sandbox-operator",
    "restricted-operator",
    "restricted-reader",
    "observer",
    "untrusted-agent",
]
Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]{0,9}$")]
Account = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]{1,11}-[a-z][a-z0-9]{0,9}$")]
Absolute = Annotated[str, Field(pattern=r"^/")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Groups(_Strict):
    """The Unix group of each human class."""

    admins: str
    researchers: str


class Service(_Strict):
    """A service: its account, UID, configuration file, secrets and state."""

    name: Name
    profile: Profile
    account: Account
    uid: PositiveInt
    config: Absolute | None
    secrets_dir: Absolute | None
    state: dict[str, Absolute]


class Job(_Strict):
    """A training job's account."""

    name: Name
    profile: Literal["gpu-trainer"]
    account: Account
    uid: PositiveInt


class Clients(_Strict):
    """Who may connect to a socket."""

    profiles: tuple[Profile | Literal["gpu-trainer"], ...]
    accounts: tuple[str, ...]
    humans: tuple[Literal["researchers", "admins"], ...]
    human_groups: tuple[str, ...]
    root: bool


class Socket(_Strict):
    """A socket systemd creates for a service."""

    service: Name
    name: Annotated[str, Field(pattern=r"^[a-z]([a-z0-9-]{0,14}[a-z0-9])?$")]
    path: Absolute
    owner: str
    group: str
    mode: Literal["0660"]
    fd_name: str
    agent_dedicated: bool
    clients: Clients


class Principals(_Strict):
    """The whole file."""

    comment: str = Field(alias="_comment")
    schema_version: Literal[1]
    contract_version: Literal[2]
    platform: Annotated[str, Field(pattern=r"^[a-z][a-z0-9]{1,11}$")]
    groups: Groups
    services: tuple[Service, ...]
    jobs: tuple[Job, ...]
    sockets: tuple[Socket, ...]

    @model_validator(mode="after")
    def _consistent(self) -> Principals:
        people = self.services + self.jobs
        for what, values in (
            ("service or job name", [p.name for p in people]),
            ("account", [p.account for p in people]),
            ("uid", [str(p.uid) for p in people]),
            ("socket", [f"{s.service}/{s.name}" for s in self.sockets]),
            ("fd_name", [f"{s.service}/{s.fd_name}" for s in self.sockets]),
            ("socket path", [s.path for s in self.sockets]),
        ):
            twice = sorted({v for v in values if values.count(v) > 1})
            if twice:
                raise ValueError(f"{what} given twice: {', '.join(twice)}")
        groups = {"admins": self.groups.admins, "researchers": self.groups.researchers}
        for sock in self.sockets:
            expected = tuple(groups[human] for human in sock.clients.humans)
            if sock.clients.human_groups != expected:
                raise ValueError(
                    f"{sock.service}/{sock.name}: human_groups are not the groups of its humans"
                )
        return self

    def service(self, name: str) -> Service:
        """The service NAME.

        Args:
            name: The service's name in the manifest.

        Returns:
            Its entry.

        Raises:
            KeyError: No such service.
        """
        for service in self.services:
            if service.name == name:
                return service
        raise KeyError(f"no service {name} in principals.json")

    def sockets_of(self, service: str) -> tuple[Socket, ...]:
        """The sockets SERVICE serves.

        Args:
            service: The service's name.

        Returns:
            Its sockets, in the file's order.
        """
        return tuple(s for s in self.sockets if s.service == service)


def load_principals(path: Path | None = None) -> Principals:
    """Read and check ``principals.json``.

    Args:
        path: The file (`PATH` when None).

    Returns:
        The checked file.
    """
    return Principals.model_validate_json((path or PATH).read_bytes(), strict=True)
