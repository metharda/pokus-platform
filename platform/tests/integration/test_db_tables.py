"""The migrated schema has exactly the data model's tables, each with at least its columns.

The list below is the data model's, table by table; the migrations may add
columns (a time point's monotonic nanoseconds, an artifact's compression
flag) but not leave one out, and may add no table.
"""

from __future__ import annotations

import psycopg
import pytest
from potka_testing.contract import Database
from potka_testing.records import ARTIFACT, RUN, insert_run, seed

TIME_POINTS = [
    "t_queued",
    "t_provisioned",
    "t_confirm",
    "t_agent_start",
    "t_first_diagnosis",
    "t_pre_action_diagnosis",
    "t_first_mutating_action",
    "t_first_resolved",
    "t_agent_end",
    "t_final_verified",
    "t_sealed",
]
PUBLIC = {
    "lab_sessions": "id boot_id started_at fingerprint_hash fingerprint preflight_outcome errors",
    "catalog_versions": "id ref commit imported_at content_hash",
    "scenario_versions": "id catalog_version_id scenario_id version title risk_level isolation"
    " blueprint split tags public_meta",
    "taxonomy_versions": "id version labels hash",
    "blueprints": "id name version image_aliases image_fingerprints nodes",
    "derived_images": "id name version alias fingerprint base_alias base_fingerprint"
    " catalog_commit overlay_tree_hash builder_version leakage_lint_mode leakage_findings"
    " built_at export_artifact",
    "models": "id hf_repo revision files quantization license registered_at",
    "adapters": "id name base_model_id path sha256 lineage",
    "datasets": "sha256 provenance build_dir built_by published_at",
    "training_runs": "id job submission arbiter_run_id spec_sha256 status transitions adapter_id"
    " submitted_by submitted_at finished_at",
    "contamination_flags": "id run_id adapter_id scenario_template dataset_sha256"
    " evaluator_version created_at",
    "deployments": "id model_id adapter_ids profile serving_args vllm_image_digest spec_hash",
    "deployment_activations": "id deployment_id served_model_name started_at ready_at ended_at"
    " verification",
    "agent_configs": "id name version prompt_template_hash prompt_artifact mode budgets sampling"
    " tool_registry_version tool_subset evidence_profile policy_bundle_hash hash",
    "campaigns": "id name submitter spec spec_hash official official_reasons attestation_id state"
    " created_at platform_version catalog_version_id deployment_id agent_config_id approval_mode"
    " run_mode concurrency seed",
    "runs": "id campaign_id repetition seed_sampling session_id phase validity invalid_reason"
    " outcome official retry_of trace_id manifest_artifact signature_artifact chain_head sealed_at "
    + " ".join(TIME_POINTS),
    "sealed_run_scenarios": "run_id scenario_version_id instance_index sealed_at",
    "run_events": "id run_id seq type source time data envelope prev_hash hash",
    "agent_steps": "id run_id step kind content_artifact tokens created_at",
    "diagnoses": "id run_id step label root_cause affected_component confidence evidence_refs"
    " is_pre_action is_final created_at",
    "llm_calls": "id run_id step deployment_activation_id request_artifact response_artifact"
    " logprobs_artifact prompt_tokens completion_tokens ttft_ms total_ms finish_reason"
    " vllm_request_id span_id",
    "llm_usage": "id user deployment_activation_id prompt_tokens completion_tokens total_ms"
    " created_at",
    "tool_calls": "id run_id step caller tool tool_version args meta valid validation_error"
    " created_at",
    "policy_decisions": "id tool_call_id effect rule_id reason input_hash bundle_hash created_at",
    "approvals": "id tool_call_id mode state decided_by decision comment requested_at decided_at",
    "executions": "id tool_call_id node_alias instance_name argv exit_code status duration_ms"
    " stdout_artifact stderr_artifact truncated post_check",
    "probe_results": "id run_id phase probe params passed detail output_artifact created_at",
    "artifacts": "sha256 size media_type created_at created_by",
    "annotations": "id run_id author kind body created_at",
    "evaluation_jobs": "id kind params requested_by state created_at started_at finished_at error",
    "evaluations": "id run_id evaluator_version taxonomy_version_id scores created_at",
    "eval_snapshots": "id params content_hash manifest_artifact evaluator_version created_at",
    "eval_outputs": "id kind snapshot_id artifacts created_at",
    "api_keys": "id user key_hash created_at revoked_at",
}
RESTRICTED = {
    "run_assignments": "run_id scenario_version_id instance_index seed_scenario parameters gt_hash",
    "scenario_payloads": "scenario_version_id scenario_doc ground_truth_doc",
}


def columns(database: Database, schema: str) -> dict[str, set[str]]:
    with database.connect("potka_migrator") as conn:
        rows = conn.execute(
            "SELECT c.table_name, c.column_name FROM information_schema.columns c"
            " JOIN information_schema.tables t USING (table_schema, table_name)"
            " WHERE c.table_schema = %s AND t.table_type = 'BASE TABLE'"
            " AND c.table_name <> 'alembic_version'",
            (schema,),
        ).fetchall()
    found: dict[str, set[str]] = {}
    for table, column in rows:
        found.setdefault(table, set()).add(column)
    return found


def test_every_table_and_column_of_the_data_model_exists(migrated_database: Database) -> None:
    assert len(PUBLIC) == 34
    for schema, expected in [("public", PUBLIC), ("restricted", RESTRICTED)]:
        found = columns(migrated_database, schema)
        assert sorted(found) == sorted(expected), schema
        missing = {
            table: sorted(set(names.split()) - found[table])
            for table, names in expected.items()
            if set(names.split()) - found[table]
        }
        assert missing == {}, schema


def test_the_added_columns_are_the_documented_ones(migrated_database: Database) -> None:
    found = columns(migrated_database, "public")
    added = {
        table: sorted(found[table] - set(names.split()))
        for table, names in PUBLIC.items()
        if found[table] - set(names.split())
    }
    assert added == {
        "artifacts": ["compressed"],
        "runs": sorted(f"{point}_ns" for point in TIME_POINTS),
    }
    assert columns(migrated_database, "restricted") == {
        table: set(names.split()) for table, names in RESTRICTED.items()
    }


# The columns the data model gives a type, with that type.
TYPED = {
    "jsonb": "lab_sessions.fingerprint lab_sessions.errors scenario_versions.public_meta"
    " taxonomy_versions.labels blueprints.image_aliases blueprints.image_fingerprints"
    " blueprints.nodes derived_images.leakage_findings models.files adapters.lineage"
    " datasets.provenance training_runs.submission training_runs.transitions"
    " deployments.serving_args deployment_activations.verification agent_configs.budgets"
    " agent_configs.sampling agent_configs.evidence_profile campaigns.spec"
    " campaigns.official_reasons run_events.data tool_calls.args tool_calls.meta executions.argv"
    " executions.post_check probe_results.params probe_results.detail evaluation_jobs.params"
    " evaluations.scores eval_snapshots.params eval_outputs.artifacts"
    " run_assignments.parameters scenario_payloads.scenario_doc"
    " scenario_payloads.ground_truth_doc",
    "bytea": "run_events.envelope run_events.prev_hash run_events.hash",
    "ARRAY": "scenario_versions.tags deployments.adapter_ids agent_configs.tool_subset"
    " diagnoses.evidence_refs",
}


def test_the_columns_the_data_model_types_have_their_type(migrated_database: Database) -> None:
    with migrated_database.connect("potka_migrator") as conn:
        types = {
            f"{table}.{column}": kind
            for table, column, kind in conn.execute(
                "SELECT table_name, column_name, data_type FROM information_schema.columns"
                " WHERE table_schema IN ('public', 'restricted')"
            )
        }
    expected = {column: kind for kind, columns in TYPED.items() for column in columns.split()}
    assert {column: types.get(column) for column in expected} == expected


def test_a_job_s_adapters_share_its_name_but_an_adapter_is_recorded_once(
    migrated_database: Database,
) -> None:
    statement = (
        "INSERT INTO adapters (name, path, sha256, lineage) VALUES ('diag-lora', 'p', %s, '{}')"
    )
    with migrated_database.connect("potka_migrator") as conn:
        conn.execute(statement, ("1" * 64,))
        conn.execute(statement, ("2" * 64,))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(statement, ("2" * 64,))


EVENT = (
    "INSERT INTO run_events VALUES (%(id)s, %(run)s, %(seq)s, 't', 's', now(), '{}', '\\x7b7d',"
    " %(prev)s, %(hash)s)"
)
GOOD_EVENT = {"id": "0" * 26, "seq": 1, "prev": b"\1" * 32, "hash": b"\2" * 32}
CHECKS: dict[str, tuple[str, dict[str, object], str]] = {
    "event-id": (EVENT, {"id": "not-a-ulid"}, "run_events_id_check"),
    "event-seq": (EVENT, {"seq": 0}, "run_events_seq_check"),
    "prev-hash-length": (EVENT, {"prev": b"\1" * 31}, "run_events_prev_hash_check"),
    "hash-length": (EVENT, {"hash": b"\2" * 33}, "run_events_hash_check"),
    "training-status": (
        "INSERT INTO training_runs (job, submission, spec_sha256, status, transitions,"
        " submitted_by, submitted_at) VALUES ('lora', '{}', 's', 'paused', '[]', 'o', now())",
        {},
        "training_runs_status_check",
    ),
    "agent-step": (
        "INSERT INTO agent_steps (run_id, step, kind, created_at) VALUES (%(run)s, -1, 'k', now())",
        {},
        "agent_steps_step_check",
    ),
    "diagnosis-step": (
        "INSERT INTO diagnoses (run_id, step, label, evidence_refs, is_pre_action, is_final,"
        " created_at) VALUES (%(run)s, -1, 'l', '{}', false, false, now())",
        {},
        "diagnoses_step_check",
    ),
    "confidence": (
        "INSERT INTO diagnoses (run_id, step, label, confidence, evidence_refs, is_pre_action,"
        " is_final, created_at) VALUES (%(run)s, 0, 'l', 1.5, '{}', false, false, now())",
        {},
        "diagnoses_confidence_check",
    ),
    "model-call-step": (
        "INSERT INTO llm_calls (run_id, step, deployment_activation_id, request_artifact)"
        " VALUES (%(run)s, -1, 1, %(artifact)s)",
        {},
        "llm_calls_step_check",
    ),
    "tool-call-step": (
        "INSERT INTO tool_calls (run_id, step, caller, tool, tool_version, args, meta, valid,"
        " created_at) VALUES (%(run)s, -1, 'c', 't', '1', '{}', '{}', true, now())",
        {},
        "tool_calls_step_check",
    ),
    "dataset-hash": (
        "INSERT INTO datasets (sha256, provenance, build_dir, built_by)"
        " VALUES ('ABC', '{}', 'b', 'o')",
        {},
        "datasets_sha256_check",
    ),
}


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_the_records_checks_refuse_a_bad_value(migrated_database: Database, name: str) -> None:
    statement, change, constraint = CHECKS[name]
    with migrated_database.connect("potka_migrator") as conn:
        seed(conn)
        insert_run(conn)
        conn.execute(EVENT, {**GOOD_EVENT, "run": RUN})  # the same shape is accepted
        params = {**GOOD_EVENT, "run": RUN, "id": "1" * 26, "seq": 2, "hash": b"\3" * 32}
        params.update(change, artifact=ARTIFACT)
        with pytest.raises(psycopg.errors.CheckViolation) as raised:
            conn.execute(statement, params)
    assert raised.value.diag.constraint_name == constraint
