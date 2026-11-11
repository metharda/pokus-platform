"""``endpoints.toml``: the addresses, ports and paths infra gives the platform.

The model follows the file's JSON Schema in pokus-infrastructure's
``contract/`` at ``INFRA_PIN``: every field, nothing else. No address, port
or path the file gives has a default in code.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

PATH = Path("/etc/pinka/platform/endpoints.toml")
Absolute = Annotated[str, Field(pattern=r"^/")]
Address = Annotated[str, Field(pattern=r"^[0-9.]+:[0-9]{1,5}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Lab(_Strict):
    """The lab facts."""

    machine: str
    lab_mode: Literal["production", "staging"]
    node_role: Literal["full", "lab", "inference"]


class Postgres(_Strict):
    """The database's sockets and roles."""

    socket_dir: Absolute
    reader_socket_dir: Absolute
    database: str
    migrator_role: str
    reader_role: str
    grafana_role: str
    roles: dict[str, str]


class Telemetry(_Strict):
    """The OTLP endpoints, the agent's OTLP socket and the query proxy."""

    otlp_grpc: Address
    otlp_http: Address
    vm_collector_grpc: Address
    vm_collector_http: Address
    agent_otlp_socket: Absolute
    query_socket: Absolute


class Inference(_Strict):
    """The inference API."""

    mode: Literal["local", "remote", "scripted"]
    researcher_endpoint: Address
    api_url: str
    control_url: str | None = None


class Opa(_Strict):
    """The policy decision API."""

    url: str
    decision_path: str | None = None


class Incus(_Strict):
    """The Incus API, certificates, projects and run-network helper."""

    url: str
    cert_dir: Absolute
    projects: tuple[str, ...]
    run_network_socket: Absolute
    cpuset_targets: str | None = None


class Gpu(_Strict):
    """The GPU arbiter."""

    arbiter_socket: Absolute


class Signing(_Strict):
    """The signing key."""

    key_file: Absolute
    public_key_file: Absolute
    published_keys_dir: Absolute


class Attestations(_Strict):
    """The attestation directories."""

    trusted_keys_dir: Absolute
    attestations_dir: Absolute


class Sessions(_Strict):
    """The session records and the fingerprint command."""

    records_dir: Absolute
    fingerprint_command: tuple[str, ...]


class Endpoints(_Strict):
    """The whole file; a section is absent when the manifest does not require it."""

    schema_version: Literal[1]
    platform: Annotated[str, Field(pattern=r"^[a-z][a-z0-9]{1,11}$")]
    lab: Lab
    postgres: Postgres | None = None
    telemetry: Telemetry | None = None
    inference: Inference | None = None
    opa: Opa | None = None
    incus: Incus | None = None
    gpu: Gpu | None = None
    signing: Signing
    attestations: Attestations
    sessions: Sessions
    ports: dict[str, Annotated[int, Field(ge=1, le=65535)]]


def load_endpoints(path: Path | None = None) -> Endpoints:
    """Read and check ``endpoints.toml``.

    Args:
        path: The file (`PATH` when None).

    Returns:
        The checked file.

    Raises:
        ValueError: The file is not TOML, holds a date or a time (no value of
            the contract is one), or does not match the model (pydantic's
            ``ValidationError``).
        OSError: The file cannot be read.
    """
    with (path or PATH).open("rb") as file:
        data = tomllib.load(file)
    # As JSON, so arrays become the model's tuples under strict validation.
    try:
        text = json.dumps(data)
    except TypeError:
        raise ValueError("endpoints.toml holds a date or a time") from None
    return Endpoints.model_validate_json(text, strict=True)
