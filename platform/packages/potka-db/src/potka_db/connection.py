"""Connections to the platform's database, from ``endpoints.toml`` alone.

A service connects through the socket directory of ``endpoints.toml``'s
``postgres`` section, to its database, as its own role, with the password
infra writes to ``/etc/pinka/secrets/postgres/<role>``. The migration hook
connects the same way as the migrator role.

Nothing comes from the environment: libpq would take any parameter not
given explicitly from a ``PG*`` variable (``PGOPTIONS``, ``PGSERVICE``,
``PGSSLMODE`` and others), so every ``PG*`` variable is removed from the
process's environment before a connection's parameters are built.

The connection's ``application_name`` is ``potka-<service>``: the service's
name only, never a value of a run or a scenario. The parameters hide the
password from their ``repr`` and ``str``, and potka's logs write them as
their type's name.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from potka_core.endpoints import Endpoints
from potka_core.settings import read_secret
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

PASSWORD_DIR = Path("/etc/pinka/secrets/postgres")
MIGRATE = "migrate"
# A service's name in the manifest (contract v2), or "migrate".
SERVICE_NAME = re.compile(r"[a-z][a-z0-9]{0,9}")
CONNECT_TIMEOUT_SECONDS = "10"


class Params(dict[str, str]):
    """libpq parameters whose ``repr`` and ``str`` never show the password."""

    def __repr__(self) -> str:
        """The parameters, the password replaced."""
        shown = {k: ("<hidden>" if k == "password" else v) for k, v in self.items()}
        return f"Params({shown!r})"

    __str__ = __repr__


def clean_environment() -> None:
    """Remove every ``PG*`` variable, which libpq would read, from the environment."""
    for name in [name for name in os.environ if name.startswith("PG")]:
        del os.environ[name]


def application_name(service: str) -> str:
    """The ``application_name`` of SERVICE's connections.

    Args:
        service: The service's name in the manifest, or ``migrate``.

    Returns:
        ``potka-<service>``.

    Raises:
        TypeError: SERVICE is not a plain string.
        ValueError: SERVICE is not a service name.
    """
    if type(service) is not str:
        raise TypeError("a service name is a plain string")
    if not SERVICE_NAME.fullmatch(service):
        raise ValueError("not a service name")
    return f"potka-{service}"


def connection_params(
    endpoints: Endpoints, service: str, password_dir: Path = PASSWORD_DIR
) -> Params:
    """The connection parameters of SERVICE (``migrate``: the migrator).

    Args:
        endpoints: ``endpoints.toml``.
        service: The service's name, or ``migrate``.
        password_dir: Where infra writes the roles' password files.

    Returns:
        libpq parameters: the socket directory, the database, the role, its
        password, the ``application_name`` and a connect timeout.

    Raises:
        ValueError: ``endpoints.toml`` has no ``postgres`` section, or SERVICE
            has no database role there.
        potka_core.settings.SettingsError: The password file cannot be read.
    """
    name = application_name(service)
    postgres = endpoints.postgres
    if postgres is None:
        raise ValueError("endpoints.toml has no postgres section")
    role = postgres.migrator_role if service == MIGRATE else postgres.roles.get(service)
    if role is None:
        raise ValueError(f"the service {service} has no database role in endpoints.toml")
    password = read_secret(password_dir, role)
    clean_environment()
    return Params(
        host=postgres.socket_dir,
        dbname=postgres.database,
        user=role,
        password=password.get_secret_value(),
        application_name=name,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
    )


def engine(params: dict[str, str]) -> Engine:
    """An engine that opens a new connection with PARAMS each time (no pool).

    Args:
        params: libpq parameters (:func:`connection_params`).

    Returns:
        The engine (SQLAlchemy Core on psycopg 3).
    """
    clean_environment()
    return create_engine(
        URL.create("postgresql+psycopg"), connect_args=dict(params), poolclass=NullPool
    )
