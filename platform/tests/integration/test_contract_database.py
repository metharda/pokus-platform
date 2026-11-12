"""The test database is the database the contract describes: roles, memberships, owners, revokes.

The expected values are written out from pokus-infrastructure's contract
(docs/contracts.md, PostgreSQL) and the manifest's services and profiles.
"""

from __future__ import annotations

import psycopg
import pytest
from potka_testing.contract import Database

LOGINS = {
    "potka_orch",
    "potka_gw",
    "potka_exec",
    "potka_inject",
    "potka_verify",
    "potka_eval",
    "potka_migrator",
    "potka_reader",
    "potka_grafana",
}
GROUPS = {
    "potka_controller",
    "potka_inference_broker",
    "potka_sandbox_operator",
    "potka_restricted_operator",
    "potka_restricted_reader",
}
MEMBERSHIPS = {
    ("potka_orch", "potka_controller"),
    ("potka_gw", "potka_inference_broker"),
    ("potka_exec", "potka_sandbox_operator"),
    ("potka_inject", "potka_restricted_operator"),
    ("potka_verify", "potka_restricted_operator"),
    ("potka_eval", "potka_restricted_reader"),
}


def test_the_roles_and_memberships_are_the_contract_s(contract_database: Database) -> None:
    with contract_database.server.connect() as conn:
        roles = dict(
            conn.execute(
                "SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname LIKE 'potka\\_%'"
                " AND rolname NOT LIKE 'potka\\_test\\_%'"
            ).fetchall()
        )
        members = set(
            conn.execute(
                "SELECT m.rolname, r.rolname FROM pg_auth_members a"
                " JOIN pg_roles r ON r.oid = a.roleid JOIN pg_roles m ON m.oid = a.member"
                " WHERE m.rolname LIKE 'potka\\_%' OR r.rolname LIKE 'potka\\_%'"
            ).fetchall()
        )
    assert roles == {**dict.fromkeys(LOGINS, True), **dict.fromkeys(GROUPS, False)}
    assert members == MEMBERSHIPS
    assert "potka_agent" not in roles


def test_the_migrator_owns_the_database_and_both_schemas(contract_database: Database) -> None:
    with contract_database.connect("potka_migrator") as conn:
        owner = conn.execute(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()"
        ).fetchone()
        schemas = dict(
            conn.execute(
                "SELECT nspname, pg_get_userbyid(nspowner) FROM pg_namespace"
                " WHERE nspname IN ('public', 'restricted')"
            ).fetchall()
        )
    assert owner == ("potka_migrator",)
    assert schemas == {"public": "potka_migrator", "restricted": "potka_migrator"}


def test_public_has_no_database_privilege_and_no_service_role_a_schema_privilege(
    contract_database: Database,
) -> None:
    with contract_database.connect("potka_migrator") as conn:
        database = conn.execute(
            "SELECT a.privilege_type FROM pg_database d, aclexplode(d.datacl) a"
            " WHERE d.datname = current_database() AND a.grantee = 0"
        ).fetchall()
        schemas = conn.execute(
            "SELECT n.nspname, a.privilege_type, a.grantee FROM pg_namespace n,"
            " aclexplode(n.nspacl) a WHERE n.nspname IN ('public', 'restricted')"
            " AND a.grantee <> n.nspowner"
            " AND (a.grantee = 0 OR pg_get_userbyid(a.grantee) LIKE 'potka\\_%')"
        ).fetchall()
    assert database == []
    assert schemas == [("public", "USAGE", 0)]


@pytest.mark.parametrize("login", sorted(LOGINS))
def test_every_login_role_connects_with_its_password(
    contract_database: Database, login: str
) -> None:
    with contract_database.connect(login) as conn:
        assert conn.execute("SELECT current_user").fetchone() == (login,)
    params = contract_database.params(login)
    params["password"] = "not-" + params["password"]
    with pytest.raises(psycopg.OperationalError, match="password authentication failed"):
        psycopg.connect(**params)


def test_the_login_roles_have_no_attribute_and_only_connect(contract_database: Database) -> None:
    with contract_database.server.connect() as conn:
        attributes = conn.execute(
            "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s) AND (rolsuper OR rolcreatedb"
            " OR rolcreaterole OR rolbypassrls OR rolreplication OR NOT rolinherit)",
            (sorted(LOGINS | GROUPS),),
        ).fetchall()
        privileges = {
            (role, privilege): conn.execute(
                "SELECT has_database_privilege(%s, %s, %s)",
                (role, contract_database.name, privilege),
            ).fetchone()
            == (True,)
            for role in sorted(LOGINS)
            for privilege in ("CONNECT", "CREATE", "TEMPORARY")
        }
    assert attributes == []
    owner = {"potka_migrator"}
    assert privileges == {
        (role, privilege): privilege == "CONNECT" or role in owner
        for role in sorted(LOGINS)
        for privilege in ("CONNECT", "CREATE", "TEMPORARY")
    }
