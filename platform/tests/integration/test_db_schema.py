"""The migrated schema: it matches the Core tables, and runs carry nothing of their scenario."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from potka_db.connection import engine
from potka_db.tables import metadata
from potka_testing.contract import Database
from potka_testing.records import RUN, assign, insert_run, seed


def include(obj: Any, name: str | None, kind: str, reflected: bool, compare_to: Any) -> bool:
    return not (kind == "table" and name == "alembic_version")


def test_the_core_tables_are_the_migrated_tables(migrated_database: Database) -> None:
    params = {k: str(v) for k, v in migrated_database.params("potka_migrator").items()}
    with engine(params).connect() as conn:
        context = MigrationContext.configure(
            conn,
            opts={"compare_type": True, "include_schemas": True, "include_object": include},
        )
        assert compare_metadata(context, metadata) == []


def test_runs_carry_no_scenario_field(migrated_database: Database) -> None:
    with migrated_database.connect("potka_migrator") as conn:
        columns = [
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = 'public' AND table_name = 'runs'"
            )
        ]
    assert columns
    assert [c for c in columns if "scenario" in c or "instance" in c] == []


ULID_CASES = ["not-a-ulid", RUN.lower(), "8" + RUN[1:], RUN[:-1] + "I", RUN[:-1] + "L"]
ULID_CASES += [RUN[:-1] + "O", RUN[:-1] + "U", RUN[:-1], RUN + "0"]
ID_CASES: list[tuple[str, dict[str, object], str]] = [
    (f"id-{n}", {"id": bad}, "runs_id_check") for n, bad in enumerate(ULID_CASES)
]
RUN_CASES: list[tuple[str, dict[str, object], str]] = [
    *ID_CASES,
    ("phase", {"phase": "paused"}, "runs_phase_check"),
    ("validity", {"validity": "maybe"}, "runs_validity_check"),
    ("outcome-empty-budget", {"outcome": "budget_exhausted:"}, "runs_outcome_check"),
    ("outcome-newline", {"outcome": "budget_exhausted:\n"}, "runs_outcome_check"),
    ("outcome", {"outcome": "solved"}, "runs_outcome_check"),
    ("repetition", {"repetition": -1}, "runs_repetition_check"),
    ("chain-head", {"chain_head": b"\0" * 31}, "runs_chain_head_check"),
    ("campaign", {"campaign_id": "0" * 26}, "runs_campaign_id_fkey"),
    ("sealed-phase-no-time", {"phase": "sealed"}, "runs_sealed_phase"),
    ("sealed-time-other-phase", {"sealed_at": datetime.now(UTC)}, "runs_sealed_phase"),
    (
        "sealed-without-manifest",
        {"phase": "sealed", "sealed_at": datetime.now(UTC), "chain_head": b"\0" * 32},
        "runs_sealed_record",
    ),
    ("valid-with-reason", {"validity": "valid", "invalid_reason": "x"}, "runs_invalid_reason"),
    ("invalid-without-reason", {"validity": "invalid"}, "runs_invalid_reason"),
    ("reason-without-validity", {"invalid_reason": "x"}, "runs_invalid_reason"),
    ("time-without-ns", {"t_queued": datetime.now(UTC)}, "runs_time_points"),
    ("ns-without-time", {"t_agent_end_ns": 5}, "runs_time_points"),
]


@pytest.mark.parametrize(
    ("values", "constraint"), [c[1:] for c in RUN_CASES], ids=[c[0] for c in RUN_CASES]
)
def test_a_run_s_fields_are_checked(
    migrated_database: Database, values: dict[str, object], constraint: str
) -> None:
    with migrated_database.connect("potka_migrator") as conn:
        seed(conn)
        with pytest.raises(psycopg.errors.IntegrityError) as raised:
            insert_run(conn, **values)
    assert raised.value.diag.constraint_name == constraint


PHASES = [
    "queued", "provisioning", "preconditions", "baseline", "setup", "injecting", "confirming",
    "settling", "evidence", "agent", "verifying", "collecting", "teardown",
]  # fmt: skip
OUTCOMES = ["diagnosed", "abstained", "protocol_failure", "finished", "budget_exhausted:tokens"]


def test_every_valid_run_state_is_accepted(migrated_database: Database) -> None:
    rows: list[dict[str, object]] = [{"phase": phase} for phase in PHASES]
    rows += [{"phase": "teardown", "outcome": outcome} for outcome in OUTCOMES]
    rows += [{"validity": "valid"}, {"validity": "invalid", "invalid_reason": "setup_failed"}]
    rows += [{"t_queued": datetime.now(UTC), "t_queued_ns": 1}]
    with migrated_database.connect("potka_migrator") as conn:
        seed(conn)
        for number, values in enumerate(rows):
            insert_run(conn, id=f"01HZY0000000000000000000{number:02d}", **values)


OTHER_CASES = {
    "campaign-id": (
        "UPDATE campaigns SET id = 'campaign-1'", "campaigns_id_check"),
    "concurrency": ("UPDATE campaigns SET concurrency = 0", "campaigns_concurrency_check"),
    "attestation": (
        "UPDATE campaigns SET attestation_id = 'qa-1'", "campaigns_attestation_id_check"),
    "approval-mode": (
        "UPDATE campaigns SET approval_mode = 'auto'", "campaigns_approval_mode_check"),
    "run-mode": ("UPDATE campaigns SET run_mode = 'measure'", "campaigns_run_mode_check"),
    "artifact-hash": ("UPDATE artifacts SET sha256 = 'XYZ'", "artifacts_sha256_check"),
    "artifact-size": ("UPDATE artifacts SET size = -1", "artifacts_size_check"),
    "catalog-commit": (
        "UPDATE catalog_versions SET commit = 'abc'", "catalog_versions_commit_check"),
    "split": ("UPDATE scenario_versions SET split = 'holdout'", "scenario_versions_split_check"),
    "digest": (
        "UPDATE deployments SET vllm_image_digest = 'sha256:x'",
        "deployments_vllm_image_digest_check",
    ),
    "preflight": (
        "INSERT INTO lab_sessions (boot_id, started_at, preflight_outcome, errors)"
        " VALUES ('b', now(), 'fine', '{}')",
        "lab_sessions_preflight_outcome_check",
    ),
    "assignment-instance": (
        "UPDATE restricted.run_assignments SET instance_index = -1",
        "run_assignments_instance_index_check",
    ),
    "second-assignment": (
        "INSERT INTO restricted.run_assignments SELECT * FROM restricted.run_assignments",
        "run_assignments_pkey",
    ),
    "declassified-run": (
        "INSERT INTO sealed_run_scenarios VALUES ('0' || repeat('0', 25), 1, 0, now())",
        "sealed_run_scenarios_run_id_fkey",
    ),
    "declassified-instance": (
        "INSERT INTO sealed_run_scenarios VALUES (%(run)s, 1, -1, now())",
        "sealed_run_scenarios_instance_index_check",
    ),
    "payload-scenario": (
        "INSERT INTO restricted.scenario_payloads VALUES (99, '{}', '{}')",
        "scenario_payloads_scenario_version_id_fkey",
    ),
    "catalog-ref-commit": (
        "INSERT INTO catalog_versions (ref, commit, imported_at, content_hash)"
        " SELECT ref, commit, now(), 'h' FROM catalog_versions",
        "catalog_versions_ref_commit_key",
    ),
    "model": (
        "INSERT INTO models (hf_repo, revision, files, registered_at)"
        " VALUES ('example/model', 'r', '{}', now())",
        "models_hf_repo_revision_quantization_key",
    ),
}  # fmt: skip


@pytest.mark.parametrize("name", sorted(OTHER_CASES))
def test_the_other_tables_checks_and_keys(migrated_database: Database, name: str) -> None:
    statement, constraint = OTHER_CASES[name]
    with migrated_database.connect("potka_migrator") as conn:
        seed(conn)
        insert_run(conn)
        assign(conn)
        with pytest.raises(psycopg.errors.IntegrityError) as raised:
            conn.execute(statement, {"run": RUN})
    assert raised.value.diag.constraint_name == constraint


def test_what_the_keys_allow(migrated_database: Database) -> None:
    with migrated_database.connect("potka_migrator") as conn:
        seed(conn)
        conn.execute(
            "INSERT INTO catalog_versions (ref, commit, imported_at, content_hash)"
            " VALUES ('catalog-v2', %s, now(), 'h'), ('main', %s, now(), 'h')",
            ("c" * 40, "d" * 64),
        )
        conn.execute(
            "INSERT INTO agent_configs (name, version, prompt_template_hash, prompt_artifact,"
            " mode, budgets, sampling, tool_registry_version, tool_subset, evidence_profile,"
            " policy_bundle_hash, hash) SELECT 'other', version, prompt_template_hash,"
            " prompt_artifact, mode, budgets, sampling, tool_registry_version, tool_subset,"
            " evidence_profile, policy_bundle_hash, hash FROM agent_configs"
        )
        for quantization in ("awq", "fp8"):
            conn.execute(
                "INSERT INTO models (hf_repo, revision, files, quantization, registered_at)"
                " VALUES ('example/model', 'r', '{}', %s, now())",
                (quantization,),
            )


def test_identity_columns_are_generated_always(migrated_database: Database) -> None:
    expected = {
        f"{table.name}.{column.name}"
        for table in metadata.tables.values()
        for column in table.columns
        if column.identity is not None and column.identity.always
    }
    with migrated_database.connect("potka_migrator") as conn:
        found = conn.execute(
            "SELECT c.relname || '.' || a.attname, a.attidentity FROM pg_attribute a"
            " JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname IN ('public', 'restricted') AND a.attidentity <> ''"
        ).fetchall()
    assert expected
    assert dict(found) == dict.fromkeys(expected, "a")


RUN_COLUMNS = (
    "id campaign_id repetition seed_sampling session_id phase validity invalid_reason outcome"
    " official retry_of trace_id manifest_artifact signature_artifact chain_head sealed_at"
)
TIME_POINTS = (
    "queued provisioned confirm agent_start first_diagnosis pre_action_diagnosis"
    " first_mutating_action first_resolved agent_end final_verified sealed"
)


def test_runs_have_exactly_the_data_model_s_columns(migrated_database: Database) -> None:
    times = [f"t_{p}{suffix}" for p in TIME_POINTS.split() for suffix in ("", "_ns")]
    with migrated_database.connect("potka_migrator") as conn:
        columns = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = 'public' AND table_name = 'runs'"
            )
        }
    assert columns == {*RUN_COLUMNS.split(), *times}


def test_no_foreign_key_leads_from_public_to_restricted(migrated_database: Database) -> None:
    with migrated_database.connect("potka_migrator") as conn:
        keys = conn.execute(
            "SELECT conname FROM pg_constraint k"
            " JOIN pg_class f ON f.oid = k.conrelid"
            " JOIN pg_namespace fn ON fn.oid = f.relnamespace"
            " JOIN pg_class t ON t.oid = k.confrelid"
            " JOIN pg_namespace tn ON tn.oid = t.relnamespace"
            " WHERE k.contype = 'f' AND fn.nspname = 'public' AND tn.nspname = 'restricted'"
        ).fetchall()
    assert keys == []


def test_a_run_assignment_needs_its_run_and_its_scenario(migrated_database: Database) -> None:
    with migrated_database.connect("potka_migrator") as conn:
        seed(conn)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            assign(conn)
        insert_run(conn)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            assign(conn, scenario_version_id=99)
        assign(conn)
