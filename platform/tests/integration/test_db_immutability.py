"""Sealed runs cannot change; insert-only tables never change; truncation is refused."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from potka_db.connection import engine
from potka_db.sealing import seal
from potka_testing.contract import Database
from potka_testing.records import ARTIFACT, RUN, assign, insert_run, seed

SEALED_AT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
Conn = psycopg.Connection[tuple[Any, ...]]
# One row of each run-scoped table, for RUN (tool call 1 is the run's).
ROWS = {
    "run_events": "INSERT INTO run_events VALUES ('01HZY00000000000000EVENT01', %(run)s, 1,"
    " 'potka.orch.x', '/potka/orch', now(), '{}', '\\x7b7d', %(h32)s, %(h32b)s)",
    "agent_steps": "INSERT INTO agent_steps (run_id, step, kind, created_at)"
    " VALUES (%(run)s, 0, 'turn', now())",
    "diagnoses": "INSERT INTO diagnoses (run_id, step, label, evidence_refs, is_pre_action,"
    " is_final, created_at) VALUES (%(run)s, 0, 'l', '{}', false, false, now())",
    "llm_calls": "INSERT INTO llm_calls (run_id, step, deployment_activation_id,"
    " request_artifact) VALUES (%(run)s, 0, 1, %(artifact)s)",
    "tool_calls": "INSERT INTO tool_calls (run_id, step, caller, tool, tool_version, args, meta,"
    " valid, created_at) VALUES (%(run)s, 0, 'agent', 't', '1', '{}', '{}', true, now())",
    "policy_decisions": "INSERT INTO policy_decisions (tool_call_id, effect, input_hash,"
    " bundle_hash, created_at) VALUES (1, 'allow', 'i', 'b', now())",
    "approvals": "INSERT INTO approvals (tool_call_id, mode, state, requested_at)"
    " VALUES (1, 'manual', 'requested', now())",
    "executions": "INSERT INTO executions (tool_call_id, node_alias, instance_name, argv,"
    " status, truncated) VALUES (1, 'n', 'i', '[]', 'done', false)",
    "probe_results": "INSERT INTO probe_results (run_id, phase, probe, params, passed, detail,"
    " created_at) VALUES (%(run)s, 'verifying', 'p', '{}', true, '{}', now())",
    "annotations": "INSERT INTO annotations (run_id, author, kind, body, created_at)"
    " VALUES (%(run)s, 'researcher1', 'note', 'b', now())",
    "evaluations": "INSERT INTO evaluations (run_id, evaluator_version, taxonomy_version_id,"
    " scores, created_at) VALUES (%(run)s, 'v1', 1, '{}', now())",
    "contamination_flags": "INSERT INTO contamination_flags (run_id, adapter_id,"
    " scenario_template, dataset_sha256, evaluator_version, created_at)"
    " VALUES (%(run)s, 1, 't', %(dataset)s, 'v1', now())",
}
HEAD = b"\2" * 32  # the hash of the run's one event, its chain head
PARAMS = {
    "run": RUN,
    "artifact": ARTIFACT,
    "h32": b"\1" * 32,
    "h32b": HEAD,
    "dataset": "e" * 64,
}
INSERT_ONLY = [
    "run_events",
    "llm_calls",
    "tool_calls",
    "executions",
    "probe_results",
    "diagnoses",
    "policy_decisions",
]
CHANGEABLE = ["agent_steps", "approvals", "annotations", "evaluations", "contamination_flags"]
CLOSED_AT_SEALING = [
    "run_events",
    "agent_steps",
    "diagnoses",
    "llm_calls",
    "tool_calls",
    "policy_decisions",
    "approvals",
    "executions",
    "probe_results",
]
OPEN_AFTER_SEALING = ["annotations", "evaluations", "contamination_flags"]
UPDATES = {
    "run_events": "UPDATE run_events SET type = type",
    "agent_steps": "UPDATE agent_steps SET kind = kind",
    "diagnoses": "UPDATE diagnoses SET label = label",
    "llm_calls": "UPDATE llm_calls SET step = step",
    "tool_calls": "UPDATE tool_calls SET tool = tool",
    "policy_decisions": "UPDATE policy_decisions SET effect = effect",
    "approvals": "UPDATE approvals SET state = 'approved'",
    "executions": "UPDATE executions SET status = status",
    "probe_results": "UPDATE probe_results SET passed = passed",
    "annotations": "UPDATE annotations SET body = body",
    "evaluations": "UPDATE evaluations SET scores = scores",
    "contamination_flags": "UPDATE contamination_flags SET adapter_id = adapter_id",
    "restricted.run_assignments": "UPDATE restricted.run_assignments SET gt_hash = gt_hash",
    "sealed_run_scenarios": "UPDATE sealed_run_scenarios SET sealed_at = sealed_at",
    "runs": "UPDATE runs SET phase = phase",
}


def prepare(conn: Conn) -> None:
    """A campaign, RUN with its assignment, and what its rows refer to."""
    seed(conn)
    insert_run(conn)
    assign(conn)
    conn.execute(
        "INSERT INTO deployment_activations (deployment_id, served_model_name, started_at)"
        " VALUES (1, 'm', now())"
    )
    conn.execute("INSERT INTO taxonomy_versions (version, labels, hash) VALUES ('1', '{}', 'h')")
    conn.execute("INSERT INTO adapters (name, path, sha256, lineage) VALUES ('a', '/p', 's', '{}')")
    conn.execute(
        "INSERT INTO datasets (sha256, provenance, build_dir, built_by)"
        " VALUES (%s, '{}', 'b', 'o')",
        (PARAMS["dataset"],),
    )
    for statement in ROWS.values():
        conn.execute(statement, PARAMS)


def seal_it(database: Database) -> None:
    params = {k: str(v) for k, v in database.params("potka_migrator").items()}
    with engine(params).connect() as conn:
        # The chain head is the run's one event's hash (its row in ROWS).
        seal(conn, RUN, SEALED_AT, 1, ARTIFACT, ARTIFACT, HEAD)
        conn.commit()


@pytest.fixture
def records(migrated_database: Database) -> Database:
    with migrated_database.connect("potka_migrator") as conn:
        prepare(conn)
    return migrated_database


@pytest.mark.parametrize("table", [*CHANGEABLE, "restricted.run_assignments", "runs"])
def test_before_sealing_a_run_s_changeable_records_may_change(
    records: Database, table: str
) -> None:
    with records.connect("potka_migrator") as conn:
        assert conn.execute(UPDATES[table]).rowcount >= 1


@pytest.mark.parametrize("table", INSERT_ONLY)
def test_insert_only_tables_refuse_every_update_and_delete(records: Database, table: str) -> None:
    with records.connect("potka_migrator") as conn:
        deleting = f"DELETE FROM {table}"  # noqa: S608 (a fixed table name)
        for statement in (UPDATES[table], deleting):
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="insert-only"):
                conn.execute(statement)


def test_a_declassified_scenario_is_insert_only(records: Database) -> None:
    seal_it(records)  # its one row is written at sealing
    with records.connect("potka_migrator") as conn:
        for statement in (UPDATES["sealed_run_scenarios"], "DELETE FROM sealed_run_scenarios"):
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="insert-only"):
                conn.execute(statement)


@pytest.mark.parametrize("table", sorted(UPDATES))
def test_no_table_of_runs_records_may_be_truncated(records: Database, table: str) -> None:
    # The named table's trigger fires before those of the tables CASCADE reaches.
    name = table if "." in table else f"public.{table}"
    with (
        records.connect("potka_migrator") as conn,
        pytest.raises(
            psycopg.errors.ObjectNotInPrerequisiteState,
            match=re.escape(f"{name} holds runs' records and cannot be truncated"),
        ),
    ):
        conn.execute(f"TRUNCATE {table} CASCADE")


@pytest.mark.parametrize("table", CHANGEABLE)
def test_before_sealing_a_run_s_changeable_records_may_be_deleted(
    records: Database, table: str
) -> None:
    with records.connect("potka_migrator") as conn:
        assert conn.execute(f"DELETE FROM {table}").rowcount == 1  # noqa: S608 (a fixed name)


@pytest.mark.parametrize("table", sorted(UPDATES))
def test_once_sealed_no_record_of_the_run_may_change(records: Database, table: str) -> None:
    seal_it(records)
    with records.connect("potka_migrator") as conn:
        deleting = f"DELETE FROM {table}"  # noqa: S608 (a fixed table name)
        for statement in (UPDATES[table], deleting):
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
                conn.execute(statement)


def test_a_sealed_run_cannot_be_unsealed(records: Database) -> None:
    seal_it(records)
    with (
        records.connect("potka_migrator") as conn,
        pytest.raises(
            psycopg.errors.ObjectNotInPrerequisiteState, match="a sealed run cannot change"
        ),
    ):
        conn.execute("UPDATE runs SET sealed_at = NULL")


@pytest.mark.parametrize("table", CLOSED_AT_SEALING)
def test_once_sealed_the_run_s_record_tables_take_no_row(records: Database, table: str) -> None:
    seal_it(records)
    statement = ROWS[table].replace("EVENT01", "EVENT02")
    params = dict(PARAMS, h32b=b"\4" * 32)
    with (
        records.connect("potka_migrator") as conn,
        pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="records of a sealed run"),
    ):
        conn.execute(statement, params)


def test_once_sealed_the_run_takes_no_new_assignment(records: Database) -> None:
    seal_it(records)
    with (
        records.connect("potka_migrator") as conn,
        pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="sealed run"),
    ):
        assign(conn)


OTHER = "01HZY00000000000000000RN02"


def test_another_run_stays_writable_and_rows_cannot_cross_a_sealed_run(records: Database) -> None:
    with records.connect("potka_migrator") as conn:
        insert_run(conn, id=OTHER)
        for table in ("agent_steps", "tool_calls"):
            conn.execute(ROWS[table], dict(PARAMS, run=OTHER))
        conn.execute(ROWS["approvals"].replace("VALUES (1,", "VALUES (2,"), PARAMS)
    seal_it(records)
    with records.connect("potka_migrator") as conn:
        conn.execute("UPDATE agent_steps SET kind = 'turn' WHERE run_id = %s", (OTHER,))
        conn.execute("UPDATE approvals SET state = 'approved' WHERE tool_call_id = 2")
        for statement in (
            "UPDATE agent_steps SET run_id = %(other)s WHERE run_id = %(run)s",
            "UPDATE agent_steps SET run_id = %(run)s WHERE run_id = %(other)s",
            "UPDATE approvals SET tool_call_id = 2 WHERE tool_call_id = 1",
            "UPDATE approvals SET tool_call_id = 1 WHERE tool_call_id = 2",
        ):
            with pytest.raises(
                psycopg.errors.ObjectNotInPrerequisiteState, match="records of a sealed run"
            ):
                conn.execute(statement, {"run": RUN, "other": OTHER})


def test_the_controller_cannot_unseal_a_run(records: Database) -> None:
    seal_it(records)
    with (
        records.connect("potka_orch") as conn,
        pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="a sealed run cannot"),
    ):
        conn.execute("UPDATE runs SET sealed_at = NULL, phase = 'teardown'")


def test_the_checks_column_grants_are_exactly_these(records: Database) -> None:
    with records.connect("potka_migrator") as conn:
        grants = conn.execute(
            "SELECT pg_get_userbyid(a.grantee), c.relname, t.attname, a.privilege_type"
            " FROM pg_attribute t JOIN pg_class c ON c.oid = t.attrelid"
            " JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(t.attacl) a"
            " WHERE n.nspname IN ('public', 'restricted') AND t.attacl IS NOT NULL"
        ).fetchall()
    assert set(grants) == {
        ("potka_inference_broker", "runs", "id", "SELECT"),
        ("potka_inference_broker", "runs", "sealed_at", "SELECT"),
        ("potka_sandbox_operator", "runs", "id", "SELECT"),
        ("potka_sandbox_operator", "runs", "sealed_at", "SELECT"),
        ("potka_sandbox_operator", "tool_calls", "id", "SELECT"),
        ("potka_sandbox_operator", "tool_calls", "run_id", "SELECT"),
    }


@pytest.mark.parametrize("table", OPEN_AFTER_SEALING)
def test_annotations_evaluations_and_flags_stay_open(records: Database, table: str) -> None:
    seal_it(records)
    with records.connect("potka_migrator") as conn:
        conn.execute(ROWS[table], PARAMS)


def test_the_gateway_inserts_model_calls_only_for_unsealed_runs(records: Database) -> None:
    with records.connect("potka_gw") as conn:
        conn.execute(ROWS["llm_calls"], PARAMS)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM runs")
    seal_it(records)
    with (
        records.connect("potka_gw") as conn,
        pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
    ):
        conn.execute(ROWS["llm_calls"], PARAMS)


def test_the_executor_inserts_tool_records_only_for_unsealed_runs(records: Database) -> None:
    with records.connect("potka_exec") as conn:
        for table in ("tool_calls", "policy_decisions", "executions"):
            conn.execute(ROWS[table], PARAMS)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT args FROM tool_calls")
    seal_it(records)
    with records.connect("potka_exec") as conn:
        for table in ("tool_calls", "policy_decisions", "executions"):
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
                conn.execute(ROWS[table], PARAMS)
