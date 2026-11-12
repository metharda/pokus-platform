"""The platform's database as pokus-infrastructure's contract describes it.

The contract's PostgreSQL section: one database, named after the platform
and owned by the migrator role ``<platform>_migrator``, which also owns the
schemas ``public`` and ``restricted``; a login role ``<platform>_<service>``
for every service except the untrusted agent, each a member of the group
role ``<platform>_<profile>`` of its profile (hyphens as underscores); the
reader and Grafana login roles; no privilege of ``PUBLIC`` on the database,
no ``CREATE`` of ``PUBLIC`` on ``public``, nothing on ``restricted`` for
``PUBLIC``, the reader or Grafana; ``CONNECT`` for every login role; no
schema privilege for the service roles (the migrations grant them).

The services and their profiles come from the manifest. Every login role
gets a random password, and logs in with it, as in the lab.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml
from potka_db import migrate
from potka_db.roles import AGENT_PROFILE, group_role, role
from psycopg import sql

from potka_testing.postgres import Server

MANIFEST = Path(__file__).resolve().parents[2] / "platform-manifest.yaml"


@dataclass(frozen=True)
class Roles:
    """The roles of the contract, from the manifest.

    Attributes:
        platform: The platform's name.
        services: Each service with a database role, and its group role.
        migrator: The migrator role.
        reader: The researchers' reader role.
        grafana: Grafana's role.
    """

    platform: str
    services: Mapping[str, str]
    migrator: str
    reader: str
    grafana: str

    def logins(self) -> list[str]:
        """Every login role: the services', the migrator, the reader and Grafana."""
        return [
            *(role(self.platform, s) for s in self.services),
            self.migrator,
            self.reader,
            self.grafana,
        ]

    def groups(self) -> list[str]:
        """Every group role, once."""
        return sorted(set(self.services.values()))


def contract_roles(manifest: Path = MANIFEST) -> Roles:
    """The contract's roles for the manifest's services.

    Args:
        manifest: ``platform-manifest.yaml``.

    Returns:
        The roles.
    """
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    platform = data["name"]
    services = {
        s["name"]: group_role(platform, s["profile"])
        for s in data["services"]
        if s["profile"] != AGENT_PROFILE
    }
    return Roles(
        platform=platform,
        services=services,
        migrator=role(platform, "migrator"),
        reader=role(platform, "reader"),
        grafana=role(platform, "grafana"),
    )


@dataclass(frozen=True)
class Database:
    """A fresh database of the contract, with every login role's password.

    Attributes:
        server: The server.
        name: The database's name.
        roles: The contract's roles.
        passwords: Each login role's password.
    """

    server: Server
    name: str
    roles: Roles
    passwords: Mapping[str, str]

    def params(self, login: str) -> dict[str, Any]:
        """libpq parameters to log in to this database as LOGIN.

        Args:
            login: A login role.

        Returns:
            The parameters.
        """
        params = dict(self.server.params, dbname=self.name, user=login)
        params["password"] = self.passwords[login]
        return params

    def connect(self, login: str) -> psycopg.Connection[tuple[Any, ...]]:
        """An autocommit connection as LOGIN.

        Args:
            login: A login role.

        Returns:
            The connection.
        """
        return psycopg.connect(**self.params(login), autocommit=True)


def create_roles(server: Server, roles: Roles) -> dict[str, str]:
    """Create the contract's roles (or reset their passwords) and memberships.

    Args:
        server: The server.
        roles: The roles.

    Returns:
        Each login role's new password.
    """
    passwords = {login: secrets.token_hex(16) for login in roles.logins()}
    with server.connect() as conn:
        for group in roles.groups():
            if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (group,)).fetchone():
                continue
            conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(group)))
        for login, password in passwords.items():
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (login,))
            verb = "ALTER" if exists.fetchone() else "CREATE"
            statement = sql.SQL(verb + " ROLE {} LOGIN PASSWORD {}")
            conn.execute(statement.format(sql.Identifier(login), password))
        for service, group in roles.services.items():
            member = role(roles.platform, service)
            conn.execute(
                sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), sql.Identifier(member))
            )
    return passwords


def create_database(server: Server, roles: Roles, name: str) -> None:
    """Create database NAME as the contract describes it (the module's docstring).

    Args:
        server: The server.
        roles: The roles (created).
        name: The database's name.
    """
    ident = sql.Identifier
    with server.connect() as conn:
        statement = sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE template0")
        conn.execute(statement.format(ident(name), ident(roles.migrator)))
    with server.connect(name) as conn:
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(ident(name)))
        for login in roles.logins():
            statement = sql.SQL("GRANT CONNECT ON DATABASE {} TO {}")
            conn.execute(statement.format(ident(name), ident(login)))
        conn.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(ident(roles.migrator)))
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        statement = sql.SQL("CREATE SCHEMA restricted AUTHORIZATION {}")
        conn.execute(statement.format(ident(roles.migrator)))
        statement = sql.SQL("REVOKE ALL ON SCHEMA restricted FROM PUBLIC, {}, {}")
        conn.execute(statement.format(ident(roles.reader), ident(roles.grafana)))


def drop_database(server: Server, name: str) -> None:
    """Drop database NAME, ending its connections.

    Args:
        server: The server.
        name: The database's name.
    """
    with server.connect() as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


@pytest.fixture(scope="session")
def contract_passwords(postgres_server: Server) -> dict[str, str]:
    """The contract's roles on the tests' server, and their passwords."""
    return create_roles(postgres_server, contract_roles())


@pytest.fixture
def contract_database(
    postgres_server: Server, contract_passwords: dict[str, str]
) -> Iterator[Database]:
    """A fresh database of the contract, dropped after the test."""
    name = f"potka_test_{secrets.token_hex(6)}"
    roles = contract_roles()
    create_database(postgres_server, roles, name)
    try:
        yield Database(postgres_server, name, roles, contract_passwords)
    finally:
        drop_database(postgres_server, name)


@pytest.fixture
def migrated_database(contract_database: Database) -> Database:
    """A fresh database of the contract, brought to the release's newest revision."""
    login = contract_database.roles.migrator
    migrate.run({key: str(value) for key, value in contract_database.params(login).items()})
    return contract_database
