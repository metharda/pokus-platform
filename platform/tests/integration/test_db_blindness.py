"""Ground-truth blindness in the database: grants, views, the restricted schema, sealing.

``restricted`` is unreadable by every role without a grant on it (the
agent-facing, researcher and Grafana roles, and the restricted operators,
which have none yet); no view or function in ``public`` depends on it; the privileges
the migrations grant are exactly the services' needs, to the role names
pokus-infrastructure renders; infra's own restricted-schema check finds
nothing; a run's scenario reaches ``public`` only by sealing.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from potka_db.connection import engine
from potka_db.sealing import SealingError, seal
from potka_testing.contract import Database
from potka_testing.infra import GOLDEN, at_pin
from potka_testing.records import ARTIFACT, RUN, assign, insert_run, seed

PUBLIC_TABLES = """
lab_sessions catalog_versions scenario_versions taxonomy_versions artifacts models deployments
agent_configs campaigns runs sealed_run_scenarios blueprints derived_images adapters datasets
training_runs deployment_activations run_events agent_steps diagnoses llm_calls llm_usage
tool_calls policy_decisions approvals executions probe_results annotations evaluation_jobs
evaluations contamination_flags eval_snapshots eval_outputs api_keys
""".split()
RESTRICTED_TABLES = ["run_assignments", "scenario_payloads"]
STATE_TABLES = [
    "runs",
    "campaigns",
    "approvals",
    "training_runs",
    "deployment_activations",
    "evaluation_jobs",
    "api_keys",
]
VIEWS = ["run_summaries", "campaign_summaries"]
BLIND = ["potka_gw", "potka_exec", "potka_reader", "potka_grafana", "potka_inject", "potka_verify"]
SEALED_AT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
HEAD = hashlib.sha256(RUN.encode("ascii")).digest()  # a run without events
SEAL_ROW = (
    "UPDATE runs SET phase = 'sealed', sealed_at = %(at)s, manifest_artifact = %(artifact)s,"
    " signature_artifact = %(artifact)s, chain_head = %(head)s WHERE id = %(run)s"
)
SEAL_PARAMS = {"at": SEALED_AT, "artifact": ARTIFACT, "head": HEAD, "run": RUN}
# The column grants that may exist (the checks of sealed runs need them).
COLUMN_GRANTS = {
    (role, "public.runs", column, "SELECT")
    for role in ("potka_inference_broker", "potka_sandbox_operator")
    for column in ("id", "sealed_at")
} | {
    ("potka_sandbox_operator", "public.tool_calls", column, "SELECT") for column in ("id", "run_id")
}


def expected_grants() -> set[tuple[str, str, str]]:
    """(grantee, relation, privilege) as the services need them, written out."""
    grants: set[tuple[str, str, str]] = set()

    def grant(role: str, privileges: str, tables: list[str], schema: str = "public") -> None:
        grants.update((role, f"{schema}.{t}", p) for t in tables for p in privileges.split())

    grant("potka_controller", "SELECT INSERT", PUBLIC_TABLES)
    grant("potka_controller", "UPDATE", STATE_TABLES)
    grant("potka_controller", "SELECT INSERT", RESTRICTED_TABLES, "restricted")
    grant("potka_inference_broker", "INSERT", ["llm_calls", "llm_usage"])
    grant("potka_sandbox_operator", "INSERT", ["tool_calls", "policy_decisions", "executions"])
    grant("potka_restricted_reader", "SELECT", PUBLIC_TABLES)
    grant("potka_restricted_reader", "SELECT", RESTRICTED_TABLES, "restricted")
    grant("potka_restricted_reader", "INSERT", ["evaluations", "eval_snapshots"])
    grant("potka_restricted_reader", "INSERT", ["contamination_flags", "evaluation_jobs"])
    grant("potka_restricted_reader", "UPDATE", ["evaluation_jobs"])
    grant("potka_reader", "SELECT", VIEWS)
    grant("potka_grafana", "SELECT", VIEWS)
    return grants


def table_grants(conn: psycopg.Connection[tuple[Any, ...]]) -> set[tuple[str, str, str]]:
    rows = conn.execute(
        "SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,"
        " n.nspname || '.' || c.relname, a.privilege_type"
        " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) a"
        " WHERE n.nspname IN ('public', 'restricted') AND a.grantee <> c.relowner"
    ).fetchall()
    return {(who, what, privilege) for who, what, privilege in rows}


def column_grants(conn: psycopg.Connection[tuple[Any, ...]]) -> set[tuple[str, str, str, str]]:
    rows = conn.execute(
        "SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,"
        " n.nspname || '.' || c.relname, t.attname, a.privilege_type"
        " FROM pg_attribute t JOIN pg_class c ON c.oid = t.attrelid"
        " JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(t.attacl) a"
        " WHERE n.nspname IN ('public', 'restricted') AND t.attacl IS NOT NULL"
    ).fetchall()
    return {(who, what, column, privilege) for who, what, column, privilege in rows}


def test_the_table_privileges_are_exactly_the_services_needs(migrated_database: Database) -> None:
    with migrated_database.connect("potka_migrator") as conn:
        assert table_grants(conn) == expected_grants()
        assert column_grants(conn) <= COLUMN_GRANTS
        grantable = conn.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace,"
            " aclexplode(c.relacl) a WHERE n.nspname IN ('public', 'restricted')"
            " AND a.grantee <> c.relowner AND a.is_grantable"
        ).fetchone()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                " AND tablename <> 'alembic_version'"
            )
        }
        usage = conn.execute(
            "SELECT n.nspname, pg_get_userbyid(a.grantee), a.privilege_type"
            " FROM pg_namespace n, aclexplode(n.nspacl) a"
            " WHERE n.nspname = 'restricted' AND a.grantee <> n.nspowner"
        ).fetchall()
    assert set(usage) == {
        ("restricted", "potka_controller", "USAGE"),
        ("restricted", "potka_restricted_reader", "USAGE"),
    }
    assert grantable == (0,)
    assert tables == set(PUBLIC_TABLES)


@pytest.mark.parametrize("login", BLIND)
@pytest.mark.parametrize("table", RESTRICTED_TABLES)
def test_restricted_is_unreadable_by_every_role_without_a_grant(
    migrated_database: Database, login: str, table: str
) -> None:
    with (
        migrated_database.connect(login) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute(f"SELECT * FROM restricted.{table}")  # noqa: S608 (a fixed table name)


@pytest.mark.parametrize("login", ["potka_gw", "potka_exec", "potka_reader", "potka_grafana"])
@pytest.mark.parametrize("table", ["runs", "campaigns", "sealed_run_scenarios", "probe_results"])
def test_the_agent_facing_and_researcher_roles_read_no_record_table(
    migrated_database: Database, login: str, table: str
) -> None:
    with (
        migrated_database.connect(login) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute(f"SELECT * FROM public.{table}")  # noqa: S608 (a fixed table name)


DEPENDS_ON_RESTRICTED = """
SELECT 'view ' || w.oid::regclass::text
FROM pg_depend d
JOIN pg_rewrite rw ON rw.oid = d.objid
JOIN pg_class w ON w.oid = rw.ev_class
JOIN pg_namespace wn ON wn.oid = w.relnamespace
JOIN pg_class t ON t.oid = d.refobjid
JOIN pg_namespace tn ON tn.oid = t.relnamespace
WHERE d.classid = 'pg_rewrite'::regclass AND d.refclassid = 'pg_class'::regclass
  AND wn.nspname = 'public' AND tn.nspname = 'restricted'
UNION
SELECT 'view ' || w.oid::regclass::text
FROM pg_depend d
JOIN pg_rewrite rw ON rw.oid = d.objid
JOIN pg_class w ON w.oid = rw.ev_class
JOIN pg_namespace wn ON wn.oid = w.relnamespace
WHERE d.classid = 'pg_rewrite'::regclass AND wn.nspname = 'public'
  AND (
    (d.refclassid = 'pg_proc'::regclass AND d.refobjid IN (
        SELECT f.oid FROM pg_proc f JOIN pg_namespace n ON n.oid = f.pronamespace
        WHERE n.nspname = 'restricted'))
    OR (d.refclassid = 'pg_type'::regclass AND d.refobjid IN (
        SELECT y.oid FROM pg_type y JOIN pg_namespace n ON n.oid = y.typnamespace
        WHERE n.nspname = 'restricted'))
  )
UNION
SELECT 'function ' || p.oid::regprocedure::text
FROM pg_depend d
JOIN pg_proc p ON p.oid = d.objid
JOIN pg_namespace pn ON pn.oid = p.pronamespace
WHERE d.classid = 'pg_proc'::regclass AND pn.nspname = 'public'
  AND (
    (d.refclassid = 'pg_class'::regclass AND d.refobjid IN (
        SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'restricted'))
    OR (d.refclassid = 'pg_type'::regclass AND d.refobjid IN (
        SELECT y.oid FROM pg_type y JOIN pg_namespace n ON n.oid = y.typnamespace
        WHERE n.nspname = 'restricted'))
    OR (d.refclassid = 'pg_proc'::regclass AND d.refobjid IN (
        SELECT f.oid FROM pg_proc f JOIN pg_namespace n ON n.oid = f.pronamespace
        WHERE n.nspname = 'restricted'))
  )
UNION
SELECT 'function source ' || p.oid::regprocedure::text
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND (p.prosrc ~* 'restricted' OR array_to_string(p.proconfig, ' ') ~* 'restricted')
"""


def test_no_view_or_function_in_public_depends_on_restricted(migrated_database: Database) -> None:
    with migrated_database.connect("potka_migrator") as conn:
        assert conn.execute(DEPENDS_ON_RESTRICTED).fetchall() == []
        functions = conn.execute(
            "SELECT p.oid::regprocedure::text FROM pg_proc p"
            " JOIN pg_namespace n ON n.oid = p.pronamespace"
            " WHERE n.nspname IN ('public', 'restricted') AND p.prosecdef"
        ).fetchall()
    assert functions == []


def test_the_dependency_query_finds_a_view_and_a_function_that_read_restricted(
    migrated_database: Database,
) -> None:
    with migrated_database.connect("potka_migrator") as conn:
        conn.execute("CREATE VIEW public.leak AS SELECT run_id FROM restricted.run_assignments")
        conn.execute(
            "CREATE FUNCTION public.leak_count() RETURNS bigint LANGUAGE sql"
            " BEGIN ATOMIC SELECT count(*) FROM restricted.run_assignments; END"
        )
        conn.execute(
            "CREATE FUNCTION public.leak_plpgsql() RETURNS bigint LANGUAGE plpgsql"
            " AS $$ BEGIN RETURN (SELECT count(*) FROM restricted.scenario_payloads); END $$"
        )
        conn.execute(
            "CREATE FUNCTION public.leak_path() RETURNS bigint LANGUAGE plpgsql"
            " SET search_path = restricted AS $$ BEGIN RETURN (SELECT count(*) FROM"
            " scenario_payloads); END $$"
        )
        conn.execute("CREATE TYPE restricted.secret AS (x int)")
        conn.execute("CREATE FUNCTION restricted.peek() RETURNS bigint LANGUAGE sql RETURN 1")
        conn.execute("CREATE VIEW public.leak_call AS SELECT restricted.peek() AS n")
        conn.execute("CREATE VIEW public.leak_type AS SELECT NULL::restricted.secret AS s")
        conn.execute(
            "CREATE FUNCTION public.leak_typed(s restricted.secret) RETURNS int"
            " LANGUAGE sql RETURN 1"
        )
        conn.execute(
            "CREATE FUNCTION public.leak_calls() RETURNS bigint LANGUAGE sql"
            " BEGIN ATOMIC SELECT restricted.peek(); END"
        )
        found = sorted(row[0] for row in conn.execute(DEPENDS_ON_RESTRICTED))
    # A SQL-standard body is tracked as dependencies (its prosrc is empty), a
    # PL/pgSQL body only by its source text and settings: each needs its query.
    assert found == [
        "function leak_calls()",
        "function leak_count()",
        "function leak_typed(restricted.secret)",
        "function source leak_path()",
        "function source leak_plpgsql()",
        "view leak",
        "view leak_call",
        "view leak_type",
    ]


def golden(infra: Path, name: str) -> str:
    return (infra / GOLDEN / name).read_text(encoding="utf-8")


PSQL_COMMAND = re.compile(r"(^|\s)\\[a-zA-Z]+")


def without_psql_lines(script: str) -> str:
    """SCRIPT without its one psql line, which psycopg cannot run, and need not.

    psycopg stops on errors anyway. Any other psql line would change what
    the script does, so it fails the test.
    """
    meta = [line for line in script.splitlines() if PSQL_COMMAND.search(line)]
    assert meta == ["\\set ON_ERROR_STOP on"], meta
    return "\n".join(line for line in script.splitlines() if not PSQL_COMMAND.search(line))


def test_only_the_stop_on_error_line_may_be_left_out() -> None:
    assert without_psql_lines("\\set ON_ERROR_STOP on\nSELECT 1;") == "SELECT 1;"
    with pytest.raises(AssertionError):
        without_psql_lines("\\set ON_ERROR_STOP on\nSELECT 1 \\gexec")


def git(*args: str) -> str:
    done = subprocess.run(["git", *args], capture_output=True, text=True, check=True)  # noqa: S603, S607
    return done.stdout.strip()


def test_an_infra_checkout_with_local_changes_is_refused(
    infra_checkout: Path, tmp_path: Path
) -> None:
    clone = tmp_path / "infra"
    git("clone", "-q", "--shared", str(infra_checkout), str(clone))
    head = git("-C", str(infra_checkout), "rev-parse", "HEAD")
    git("-C", str(clone), "checkout", "-q", "--detach", head)
    assert at_pin(clone) == clone
    (clone / "README.md").write_text("changed\n", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="local changes"):
        at_pin(clone)


def run_infra_check(database: Database, infra: Path) -> list[str]:
    """infra's restricted-schema check for this platform, at the pin; its notices."""
    statements = without_psql_lines(golden(infra, "pokus-platform.restricted-check.sql"))
    notices: list[str] = []
    with database.server.connect(database.name) as conn:
        conn.add_notice_handler(lambda diag: notices.append(diag.message_primary or ""))
        conn.execute(statements)
    return notices


def test_infra_s_restricted_schema_check_at_the_pin_finds_nothing(
    migrated_database: Database, infra_checkout: Path
) -> None:
    assert run_infra_check(migrated_database, infra_checkout) == []


def test_infra_s_check_reports_a_forbidden_grant(
    migrated_database: Database, infra_checkout: Path
) -> None:
    with migrated_database.connect("potka_migrator") as conn:
        conn.execute("GRANT USAGE ON SCHEMA restricted TO potka_gw")
        conn.execute("GRANT SELECT ON restricted.run_assignments TO potka_inference_broker")
    notices = run_infra_check(migrated_database, infra_checkout)
    assert sorted(notices) == [
        "pinka-violation: potka_gw has privileges on schema restricted",
        "pinka-violation: potka_inference_broker has privileges on restricted.run_assignments",
    ]


def test_every_role_name_is_the_one_infra_renders_at_the_pin(
    migrated_database: Database, infra_checkout: Path
) -> None:
    bootstrap = golden(infra_checkout, "pokus-platform.bootstrap.sql")
    arrays = [
        re.findall(r"'([a-z_]+)'", body)
        for body in re.findall(r"ARRAY\[([^\]]*)\]::text\[\]", bootstrap)
    ]
    logins, groups = set(arrays[0]), set(arrays[1])
    values = re.search(r"FROM \(VALUES (.*?)\) AS t\(m, g\)", bootstrap, re.DOTALL)
    assert values is not None
    memberships = set(re.findall(r"\('(potka_[a-z_]+)', '(potka_[a-z_]+)'\)", values[1]))
    roles = migrated_database.roles
    assert set(roles.logins()) == logins
    assert set(roles.groups()) == groups
    assert {(f"potka_{s}", g) for s, g in roles.services.items()} == memberships
    with migrated_database.connect("potka_migrator") as conn:
        grantees = {who for who, _, _ in table_grants(conn)}
    assert grantees
    assert grantees <= groups | {roles.reader, roles.grafana}


@pytest.fixture
def run_to_seal(migrated_database: Database) -> Database:
    with migrated_database.connect("potka_migrator") as conn:
        seed(conn)
        conn.execute(
            "INSERT INTO artifacts (sha256, size, media_type, compressed, created_at, created_by)"
            " VALUES (%s, 1, 'application/json', false, now(), 'orch')",
            ("b" * 64,),
        )
        insert_run(conn, repetition=2)
        assign(conn)
    return migrated_database


@pytest.fixture
def orch(run_to_seal: Database) -> Iterator[Any]:
    params = {k: str(v) for k, v in run_to_seal.params("potka_orch").items()}
    with engine(params).connect() as conn:
        yield conn


def seal_run(conn: Any, run: str = RUN) -> None:
    head = hashlib.sha256(run.encode("ascii")).digest()
    seal(conn, run, SEALED_AT, 1, ARTIFACT, "b" * 64, head)


def test_sealing_declassifies_the_scenario_in_the_same_transaction(
    run_to_seal: Database, orch: Any
) -> None:
    seal_run(orch)
    orch.commit()
    with run_to_seal.connect("potka_migrator") as conn:
        row = conn.execute("SELECT * FROM sealed_run_scenarios").fetchall()
        sealed = conn.execute(
            "SELECT phase, sealed_at, t_sealed, t_sealed_ns, chain_head FROM runs"
        ).fetchone()
    assert row == [(RUN, 1, 3, SEALED_AT)]
    assert sealed == ("sealed", SEALED_AT, SEALED_AT, 1, HEAD)


def test_sealing_is_seen_by_others_only_once_committed(run_to_seal: Database, orch: Any) -> None:
    seal_run(orch)
    with run_to_seal.connect("potka_migrator") as conn:
        assert conn.execute("SELECT count(*) FROM sealed_run_scenarios").fetchone() == (0,)
    orch.rollback()
    with run_to_seal.connect("potka_migrator") as conn:
        assert conn.execute("SELECT count(*) FROM sealed_run_scenarios").fetchone() == (0,)
        assert conn.execute("SELECT sealed_at FROM runs").fetchone() == (None,)


def test_a_run_is_sealed_once_and_only_with_an_assignment(run_to_seal: Database, orch: Any) -> None:
    seal_run(orch)
    orch.commit()
    with pytest.raises(SealingError, match="sealed already"):
        seal_run(orch)
    orch.rollback()
    with run_to_seal.connect("potka_migrator") as conn:
        insert_run(conn, id="01HZY00000000000000000RN02")
    with pytest.raises(SealingError, match="no assignment"):
        seal_run(orch, "01HZY00000000000000000RN02")
    orch.rollback()
    with pytest.raises(SealingError, match="unknown"):
        seal_run(orch, "01HZY00000000000000000RN09")


def test_a_declassified_scenario_without_sealing_is_refused(run_to_seal: Database) -> None:
    with (
        run_to_seal.connect("potka_orch") as conn,
        pytest.raises(psycopg.errors.CheckViolation, match="sealed run's assignment"),
    ):
        conn.execute("INSERT INTO sealed_run_scenarios VALUES (%s, 1, 3, %s)", (RUN, SEALED_AT))


@pytest.mark.parametrize(("scenario", "instance"), [(2, 3), (1, 4)])
def test_a_declassified_scenario_must_be_the_run_s_assignment(
    run_to_seal: Database, scenario: int, instance: int
) -> None:
    conn = psycopg.connect(**run_to_seal.params("potka_orch"))
    with conn, pytest.raises(psycopg.errors.CheckViolation, match="sealed run's assignment"):
        conn.execute(SEAL_ROW, SEAL_PARAMS)
        conn.execute(
            "INSERT INTO sealed_run_scenarios VALUES (%s, %s, %s, %s)",
            (RUN, scenario, instance, SEALED_AT),
        )
        conn.commit()


def test_a_declassified_scenario_must_carry_the_run_s_sealing_time(run_to_seal: Database) -> None:
    conn = psycopg.connect(**run_to_seal.params("potka_orch"))
    with conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(SEAL_ROW, SEAL_PARAMS)
        conn.execute(
            "INSERT INTO sealed_run_scenarios VALUES (%s, 1, 3, %s)",
            (RUN, datetime(2026, 11, 11, 12, 1, tzinfo=UTC)),
        )
        conn.commit()


def test_the_declassified_row_may_come_first_in_the_transaction(run_to_seal: Database) -> None:
    with psycopg.connect(**run_to_seal.params("potka_orch")) as conn:
        conn.execute("INSERT INTO sealed_run_scenarios VALUES (%s, 1, 3, %s)", (RUN, SEALED_AT))
        conn.execute(SEAL_ROW, SEAL_PARAMS)
    with run_to_seal.connect("potka_migrator") as conn:
        assert conn.execute("SELECT sealed_at FROM runs").fetchone() == (SEALED_AT,)


def test_a_sealed_run_without_its_declassified_scenario_is_refused(run_to_seal: Database) -> None:
    with (
        run_to_seal.connect("potka_orch") as conn,
        pytest.raises(psycopg.errors.CheckViolation, match="must have its declassified scenario"),
    ):
        conn.execute(SEAL_ROW, SEAL_PARAMS)


def test_the_views_show_a_scenario_only_once_the_run_is_sealed(
    run_to_seal: Database, orch: Any
) -> None:
    query = (
        "SELECT run_id, phase, repetition, scenario_version_id, scenario_id, scenario_version,"
        " instance_index FROM run_summaries"
    )
    with run_to_seal.connect("potka_reader") as conn:
        assert conn.execute(query).fetchall() == [(RUN, "queued", None, None, None, None, None)]
        campaign = conn.execute("SELECT runs, sealed_runs FROM campaign_summaries").fetchall()
        assert campaign == [(1, 0)]
    seal_run(orch)
    orch.commit()
    with run_to_seal.connect("potka_grafana") as conn:
        assert conn.execute(query).fetchall() == [(RUN, "sealed", 2, 1, "FIXTURE-1", "1", 3)]
        assert conn.execute("SELECT sealed_runs FROM campaign_summaries").fetchall() == [(1,)]


def test_the_view_shows_no_scenario_for_a_run_whose_declassified_row_is_not_its_sealing(
    run_to_seal: Database,
) -> None:
    # A row that the database's checks would refuse, written past them by the superuser.
    with run_to_seal.server.connect(run_to_seal.name) as conn:
        conn.execute("SET session_replication_role = replica")
        conn.execute("INSERT INTO sealed_run_scenarios VALUES (%s, 1, 3, %s)", (RUN, SEALED_AT))
    with run_to_seal.connect("potka_reader") as conn:
        row = conn.execute(
            "SELECT scenario_version_id, scenario_id, instance_index FROM run_summaries"
        ).fetchone()
    assert row == (None, None, None)


def test_the_views_are_security_barriers(migrated_database: Database) -> None:
    with migrated_database.connect("potka_migrator") as conn:
        options = dict(
            conn.execute(
                "SELECT relname, reloptions FROM pg_class WHERE relname = ANY(%s)", (VIEWS,)
            ).fetchall()
        )
    assert options == {view: ["security_barrier=true"] for view in VIEWS}
