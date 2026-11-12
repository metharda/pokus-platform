"""The platform's tables as SQLAlchemy Core metadata.

The migrations create the tables; this module describes them for the
queries, and a test checks that the two agree (Alembic's
``compare_metadata`` finds no difference on a migrated database). Check
constraints and server-side defaults live in the migrations only.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Double,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP

metadata = MetaData()
TIMESTAMPTZ = TIMESTAMP(timezone=True)
TIME_POINTS = (
    "queued",
    "provisioned",
    "confirm",
    "agent_start",
    "first_diagnosis",
    "pre_action_diagnosis",
    "first_mutating_action",
    "first_resolved",
    "agent_end",
    "final_verified",
    "sealed",
)


def _id() -> Column[int]:
    return Column("id", BigInteger, Identity(always=True), primary_key=True)


def _times() -> list[Column[object]]:
    columns: list[Column[object]] = []
    for point in TIME_POINTS:
        columns.append(Column(f"t_{point}", TIMESTAMPTZ))
        columns.append(Column(f"t_{point}_ns", BigInteger))
    return columns


lab_sessions = Table(
    "lab_sessions",
    metadata,
    _id(),
    Column("boot_id", Text, nullable=False, unique=True),
    Column("started_at", TIMESTAMPTZ, nullable=False),
    Column("fingerprint_hash", Text),
    Column("fingerprint", JSONB),
    Column("preflight_outcome", Text),
    Column("errors", JSONB, nullable=False),
)

catalog_versions = Table(
    "catalog_versions",
    metadata,
    _id(),
    Column("ref", Text, nullable=False),
    Column("commit", Text, nullable=False),
    Column("imported_at", TIMESTAMPTZ, nullable=False),
    Column("content_hash", Text, nullable=False),
    UniqueConstraint("ref", "commit"),
)

scenario_versions = Table(
    "scenario_versions",
    metadata,
    _id(),
    Column("catalog_version_id", BigInteger, ForeignKey("catalog_versions.id"), nullable=False),
    Column("scenario_id", Text, nullable=False),
    Column("version", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("risk_level", Text, nullable=False),
    Column("isolation", Text, nullable=False),
    Column("blueprint", Text, nullable=False),
    Column("split", Text, nullable=False),
    Column("tags", ARRAY(Text), nullable=False),
    Column("public_meta", JSONB, nullable=False),
    UniqueConstraint("catalog_version_id", "scenario_id", "version"),
)

taxonomy_versions = Table(
    "taxonomy_versions",
    metadata,
    _id(),
    Column("version", Text, nullable=False, unique=True),
    Column("labels", JSONB, nullable=False),
    Column("hash", Text, nullable=False),
)

artifacts = Table(
    "artifacts",
    metadata,
    Column("sha256", Text, primary_key=True),
    Column("size", BigInteger, nullable=False),
    Column("media_type", Text, nullable=False),
    Column("compressed", Boolean, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    Column("created_by", Text, nullable=False),
)

models = Table(
    "models",
    metadata,
    _id(),
    Column("hf_repo", Text, nullable=False),
    Column("revision", Text, nullable=False),
    Column("files", JSONB, nullable=False),
    Column("quantization", Text),
    Column("license", Text),
    Column("registered_at", TIMESTAMPTZ, nullable=False),
    UniqueConstraint("hf_repo", "revision", "quantization", postgresql_nulls_not_distinct=True),
)

deployments = Table(
    "deployments",
    metadata,
    _id(),
    Column("model_id", BigInteger, ForeignKey("models.id"), nullable=False),
    Column("adapter_ids", ARRAY(BigInteger), nullable=False),
    Column("profile", Text, nullable=False),
    Column("serving_args", JSONB, nullable=False),
    Column("vllm_image_digest", Text, nullable=False),
    Column("spec_hash", Text, nullable=False, unique=True),
)

agent_configs = Table(
    "agent_configs",
    metadata,
    _id(),
    Column("name", Text, nullable=False),
    Column("version", Text, nullable=False),
    Column("prompt_template_hash", Text, nullable=False),
    Column("prompt_artifact", Text, ForeignKey("artifacts.sha256"), nullable=False),
    Column("mode", Text, nullable=False),
    Column("budgets", JSONB, nullable=False),
    Column("sampling", JSONB, nullable=False),
    Column("tool_registry_version", Text, nullable=False),
    Column("tool_subset", ARRAY(Text), nullable=False),
    Column("evidence_profile", JSONB, nullable=False),
    Column("policy_bundle_hash", Text, nullable=False),
    Column("hash", Text, nullable=False),
    UniqueConstraint("name", "version"),
)

campaigns = Table(
    "campaigns",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("submitter", Text, nullable=False),
    Column("spec", JSONB, nullable=False),
    Column("spec_hash", Text, nullable=False),
    Column("official", Boolean, nullable=False),
    Column("official_reasons", JSONB, nullable=False),
    Column("attestation_id", Text),
    Column("state", Text, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    Column("platform_version", Text, nullable=False),
    Column("catalog_version_id", BigInteger, ForeignKey("catalog_versions.id"), nullable=False),
    Column("deployment_id", BigInteger, ForeignKey("deployments.id"), nullable=False),
    Column("agent_config_id", BigInteger, ForeignKey("agent_configs.id"), nullable=False),
    Column("approval_mode", Text, nullable=False),
    Column("run_mode", Text, nullable=False),
    Column("concurrency", Integer, nullable=False),
    Column("seed", BigInteger, nullable=False),
)

runs = Table(
    "runs",
    metadata,
    Column("id", Text, primary_key=True),
    Column("campaign_id", Text, ForeignKey("campaigns.id"), nullable=False),
    Column("repetition", Integer, nullable=False),
    Column("seed_sampling", BigInteger, nullable=False),
    Column("session_id", BigInteger, ForeignKey("lab_sessions.id")),
    Column("phase", Text, nullable=False),
    Column("validity", Text),
    Column("invalid_reason", Text),
    Column("outcome", Text),
    Column("official", Boolean, nullable=False),
    Column("retry_of", Text, ForeignKey("runs.id")),
    *_times(),
    Column("trace_id", Text),
    Column("manifest_artifact", Text, ForeignKey("artifacts.sha256")),
    Column("signature_artifact", Text, ForeignKey("artifacts.sha256")),
    Column("chain_head", LargeBinary),
    Column("sealed_at", TIMESTAMPTZ),
    Index("runs_campaign_id", "campaign_id"),
    Index("runs_phase", "phase"),
    Index("runs_session_id", "session_id"),
    Index("runs_retry_of", "retry_of"),
)

sealed_run_scenarios = Table(
    "sealed_run_scenarios",
    metadata,
    Column("run_id", Text, ForeignKey("runs.id"), primary_key=True),
    Column("scenario_version_id", BigInteger, ForeignKey("scenario_versions.id"), nullable=False),
    Column("instance_index", Integer, nullable=False),
    Column("sealed_at", TIMESTAMPTZ, nullable=False),
    Index("sealed_run_scenarios_scenario_version_id", "scenario_version_id"),
)

run_assignments = Table(
    "run_assignments",
    metadata,
    Column("run_id", Text, ForeignKey("runs.id"), primary_key=True),
    Column("scenario_version_id", BigInteger, ForeignKey("scenario_versions.id"), nullable=False),
    Column("instance_index", Integer, nullable=False),
    Column("seed_scenario", BigInteger, nullable=False),
    Column("parameters", JSONB, nullable=False),
    Column("gt_hash", Text, nullable=False),
    Index("run_assignments_scenario_version_id", "scenario_version_id"),
    schema="restricted",
)

scenario_payloads = Table(
    "scenario_payloads",
    metadata,
    Column("scenario_version_id", BigInteger, ForeignKey("scenario_versions.id"), primary_key=True),
    Column("scenario_doc", JSONB, nullable=False),
    Column("ground_truth_doc", JSONB, nullable=False),
    schema="restricted",
)


# The records of runs, models, training and evaluation.


def _run_id() -> Column[str]:
    return Column("run_id", Text, ForeignKey("runs.id"), nullable=False)


def _tool_call_id() -> Column[int]:
    return Column("tool_call_id", BigInteger, ForeignKey("tool_calls.id"), nullable=False)


def _artifact(name: str, nullable: bool = True) -> Column[str]:
    return Column(name, Text, ForeignKey("artifacts.sha256"), nullable=nullable)


def _activation_id() -> Column[int]:
    return Column(
        "deployment_activation_id",
        BigInteger,
        ForeignKey("deployment_activations.id"),
        nullable=False,
    )


blueprints = Table(
    "blueprints",
    metadata,
    _id(),
    Column("name", Text, nullable=False),
    Column("version", Text, nullable=False),
    Column("image_aliases", JSONB, nullable=False),
    Column("image_fingerprints", JSONB, nullable=False),
    Column("nodes", JSONB, nullable=False),
    UniqueConstraint("name", "version"),
)

derived_images = Table(
    "derived_images",
    metadata,
    _id(),
    Column("name", Text, nullable=False),
    Column("version", Text, nullable=False),
    Column("alias", Text, nullable=False, unique=True),
    Column("fingerprint", Text, nullable=False),
    Column("base_alias", Text, nullable=False),
    Column("base_fingerprint", Text, nullable=False),
    Column("catalog_commit", Text, nullable=False),
    Column("overlay_tree_hash", Text, nullable=False),
    Column("builder_version", Text, nullable=False),
    Column("leakage_lint_mode", Text, nullable=False),
    Column("leakage_findings", JSONB, nullable=False),
    Column("built_at", TIMESTAMPTZ, nullable=False),
    _artifact("export_artifact"),
    UniqueConstraint("name", "version"),
)

adapters = Table(
    "adapters",
    metadata,
    _id(),
    Column("name", Text, nullable=False),
    Column("base_model_id", BigInteger, ForeignKey("models.id")),
    Column("path", Text, nullable=False),
    Column("sha256", Text, nullable=False),
    Column("lineage", JSONB, nullable=False),
    UniqueConstraint("name", "sha256"),
)

datasets = Table(
    "datasets",
    metadata,
    Column("sha256", Text, primary_key=True),
    Column("provenance", JSONB, nullable=False),
    Column("build_dir", Text, nullable=False),
    Column("built_by", Text, nullable=False),
    Column("published_at", TIMESTAMPTZ),
)

training_runs = Table(
    "training_runs",
    metadata,
    _id(),
    Column("job", Text, nullable=False),
    Column("submission", JSONB, nullable=False),
    Column("arbiter_run_id", Text),
    Column("spec_sha256", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("transitions", JSONB, nullable=False),
    Column("adapter_id", BigInteger, ForeignKey("adapters.id")),
    Column("submitted_by", Text, nullable=False),
    Column("submitted_at", TIMESTAMPTZ, nullable=False),
    Column("finished_at", TIMESTAMPTZ),
    Index("training_runs_adapter_id", "adapter_id"),
)

deployment_activations = Table(
    "deployment_activations",
    metadata,
    _id(),
    Column("deployment_id", BigInteger, ForeignKey("deployments.id"), nullable=False),
    Column("served_model_name", Text, nullable=False),
    Column("started_at", TIMESTAMPTZ, nullable=False),
    Column("ready_at", TIMESTAMPTZ),
    Column("ended_at", TIMESTAMPTZ),
    Column("verification", JSONB),
    Index("deployment_activations_deployment_id", "deployment_id"),
)

run_events = Table(
    "run_events",
    metadata,
    Column("id", Text, primary_key=True),
    _run_id(),
    Column("seq", Integer, nullable=False),
    Column("type", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("time", TIMESTAMPTZ, nullable=False),
    Column("data", JSONB, nullable=False),
    Column("envelope", LargeBinary, nullable=False),
    Column("prev_hash", LargeBinary, nullable=False),
    Column("hash", LargeBinary, nullable=False, unique=True),
    UniqueConstraint("run_id", "seq"),
)

agent_steps = Table(
    "agent_steps",
    metadata,
    _id(),
    _run_id(),
    Column("step", Integer, nullable=False),
    Column("kind", Text, nullable=False),
    _artifact("content_artifact"),
    Column("tokens", Integer),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    Index("agent_steps_run_id", "run_id"),
)

diagnoses = Table(
    "diagnoses",
    metadata,
    _id(),
    _run_id(),
    Column("step", Integer, nullable=False),
    Column("label", Text, nullable=False),
    Column("root_cause", Text),
    Column("affected_component", Text),
    Column("confidence", Double),
    Column("evidence_refs", ARRAY(Text), nullable=False),
    Column("is_pre_action", Boolean, nullable=False),
    Column("is_final", Boolean, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    Index("diagnoses_run_id", "run_id"),
)

llm_calls = Table(
    "llm_calls",
    metadata,
    _id(),
    _run_id(),
    Column("step", Integer, nullable=False),
    _activation_id(),
    _artifact("request_artifact", nullable=False),
    _artifact("response_artifact"),
    _artifact("logprobs_artifact"),
    Column("prompt_tokens", Integer),
    Column("completion_tokens", Integer),
    Column("ttft_ms", Integer),
    Column("total_ms", Integer),
    Column("finish_reason", Text),
    Column("vllm_request_id", Text),
    Column("span_id", Text),
    Index("llm_calls_run_id", "run_id"),
    Index("llm_calls_deployment_activation_id", "deployment_activation_id"),
)

llm_usage = Table(
    "llm_usage",
    metadata,
    _id(),
    Column("user", Text, nullable=False),
    _activation_id(),
    Column("prompt_tokens", Integer),
    Column("completion_tokens", Integer),
    Column("total_ms", Integer),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    Index("llm_usage_deployment_activation_id", "deployment_activation_id"),
    Index("llm_usage_user", "user"),
)

tool_calls = Table(
    "tool_calls",
    metadata,
    _id(),
    _run_id(),
    Column("step", Integer, nullable=False),
    Column("caller", Text, nullable=False),
    Column("tool", Text, nullable=False),
    Column("tool_version", Text, nullable=False),
    Column("args", JSONB, nullable=False),
    Column("meta", JSONB, nullable=False),
    Column("valid", Boolean, nullable=False),
    Column("validation_error", Text),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    Index("tool_calls_run_id", "run_id"),
)

policy_decisions = Table(
    "policy_decisions",
    metadata,
    _id(),
    _tool_call_id(),
    Column("effect", Text, nullable=False),
    Column("rule_id", Text),
    Column("reason", Text),
    Column("input_hash", Text, nullable=False),
    Column("bundle_hash", Text, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    Index("policy_decisions_tool_call_id", "tool_call_id"),
)

approvals = Table(
    "approvals",
    metadata,
    _id(),
    _tool_call_id(),
    Column("mode", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("decided_by", Text),
    Column("decision", Text),
    Column("comment", Text),
    Column("requested_at", TIMESTAMPTZ, nullable=False),
    Column("decided_at", TIMESTAMPTZ),
    Index("approvals_tool_call_id", "tool_call_id"),
)

executions = Table(
    "executions",
    metadata,
    _id(),
    _tool_call_id(),
    Column("node_alias", Text, nullable=False),
    Column("instance_name", Text, nullable=False),
    Column("argv", JSONB, nullable=False),
    Column("exit_code", Integer),
    Column("status", Text, nullable=False),
    Column("duration_ms", BigInteger),
    _artifact("stdout_artifact"),
    _artifact("stderr_artifact"),
    Column("truncated", Boolean, nullable=False),
    Column("post_check", JSONB),
    Index("executions_tool_call_id", "tool_call_id"),
)

probe_results = Table(
    "probe_results",
    metadata,
    _id(),
    _run_id(),
    Column("phase", Text, nullable=False),
    Column("probe", Text, nullable=False),
    Column("params", JSONB, nullable=False),
    Column("passed", Boolean, nullable=False),
    Column("detail", JSONB, nullable=False),
    _artifact("output_artifact"),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    Index("probe_results_run_id", "run_id"),
)

# Not named "annotations", which the module's future import defines.
run_annotations = Table(
    "annotations",
    metadata,
    _id(),
    _run_id(),
    Column("author", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("body", Text, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    Index("annotations_run_id", "run_id"),
)

evaluation_jobs = Table(
    "evaluation_jobs",
    metadata,
    _id(),
    Column("kind", Text, nullable=False),
    Column("params", JSONB, nullable=False),
    Column("requested_by", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    Column("started_at", TIMESTAMPTZ),
    Column("finished_at", TIMESTAMPTZ),
    Column("error", Text),
    Index("evaluation_jobs_state", "state"),
)

evaluations = Table(
    "evaluations",
    metadata,
    _id(),
    _run_id(),
    Column("evaluator_version", Text, nullable=False),
    Column("taxonomy_version_id", BigInteger, ForeignKey("taxonomy_versions.id"), nullable=False),
    Column("scores", JSONB, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    Index("evaluations_run_id", "run_id"),
    Index("evaluations_taxonomy_version_id", "taxonomy_version_id"),
)

contamination_flags = Table(
    "contamination_flags",
    metadata,
    _id(),
    _run_id(),
    Column("adapter_id", BigInteger, ForeignKey("adapters.id"), nullable=False),
    Column("scenario_template", Text, nullable=False),
    Column("dataset_sha256", Text, ForeignKey("datasets.sha256"), nullable=False),
    Column("evaluator_version", Text, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    Index("contamination_flags_run_id", "run_id"),
    Index("contamination_flags_adapter_id", "adapter_id"),
    Index("contamination_flags_dataset_sha256", "dataset_sha256"),
)

eval_snapshots = Table(
    "eval_snapshots",
    metadata,
    _id(),
    Column("params", JSONB, nullable=False),
    Column("content_hash", Text, nullable=False),
    _artifact("manifest_artifact", nullable=False),
    Column("evaluator_version", Text, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False),
)

eval_outputs = Table(
    "eval_outputs",
    metadata,
    _id(),
    Column("kind", Text, nullable=False),
    Column("snapshot_id", BigInteger, ForeignKey("eval_snapshots.id"), nullable=False),
    Column("artifacts", JSONB, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    Index("eval_outputs_snapshot_id", "snapshot_id"),
)

api_keys = Table(
    "api_keys",
    metadata,
    _id(),
    Column("user", Text, nullable=False),
    Column("key_hash", Text, nullable=False, unique=True),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    Column("revoked_at", TIMESTAMPTZ),
)
