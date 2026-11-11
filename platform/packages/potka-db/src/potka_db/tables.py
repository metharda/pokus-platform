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
