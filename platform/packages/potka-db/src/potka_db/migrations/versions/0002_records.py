"""The records of runs, models, training and evaluation.

Blueprints and derived images, adapters, datasets, training runs,
contamination flags, deployment activations, a run's events, agent steps,
diagnoses, model calls and ad-hoc usage, tool calls with their policy
decisions, approvals and executions, probe results, annotations,
evaluation jobs, evaluations, snapshots and outputs, and API keys. They
are in ``public``: before a run is sealed, nothing written to them may name
or derive its scenario.

A run's events carry their hash chain: ``envelope`` holds the exact bytes
that were hashed, ``prev_hash`` and ``hash`` 32 bytes each.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

ULID = "~ '^[0-7][0-9A-HJKMNP-TV-Z]{25}$'"
ID = "id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY"
ARTIFACT = "text REFERENCES public.artifacts (sha256)"
RUN = "run_id text NOT NULL REFERENCES public.runs (id)"
TOOL_CALL = "tool_call_id bigint NOT NULL REFERENCES public.tool_calls (id)"
ACTIVATION = (
    "deployment_activation_id bigint NOT NULL REFERENCES public.deployment_activations (id)"
)
# Every run-scoped table gets an index on run_id; run_events has one already,
# its unique (run_id, seq).
RUN_SCOPED = (
    "run_events",
    "agent_steps",
    "diagnoses",
    "llm_calls",
    "tool_calls",
    "probe_results",
    "annotations",
    "evaluations",
    "contamination_flags",
)
BY_TOOL_CALL = ("policy_decisions", "approvals", "executions")
# The other foreign keys and the columns the services look rows up by.
INDEXED = (
    ("deployment_activations", "deployment_id"),
    ("llm_calls", "deployment_activation_id"),
    ("llm_usage", "deployment_activation_id"),
    ("llm_usage", "user"),
    ("training_runs", "adapter_id"),
    ("contamination_flags", "adapter_id"),
    ("contamination_flags", "dataset_sha256"),
    ("evaluations", "taxonomy_version_id"),
    ("eval_outputs", "snapshot_id"),
    ("evaluation_jobs", "state"),
)

STATEMENTS = (
    f"""
CREATE TABLE public.blueprints (
    {ID},
    name text NOT NULL,
    version text NOT NULL,
    image_aliases jsonb NOT NULL,
    image_fingerprints jsonb NOT NULL,
    nodes jsonb NOT NULL,
    UNIQUE (name, version)
)""",
    f"""
CREATE TABLE public.derived_images (
    {ID},
    name text NOT NULL,
    version text NOT NULL,
    alias text NOT NULL UNIQUE,
    fingerprint text NOT NULL,
    base_alias text NOT NULL,
    base_fingerprint text NOT NULL,
    catalog_commit text NOT NULL,
    overlay_tree_hash text NOT NULL,
    builder_version text NOT NULL,
    leakage_lint_mode text NOT NULL,
    leakage_findings jsonb NOT NULL,
    built_at timestamptz NOT NULL,
    export_artifact {ARTIFACT},
    UNIQUE (name, version)
)""",
    f"""
CREATE TABLE public.adapters (
    {ID},
    name text NOT NULL,
    base_model_id bigint REFERENCES public.models (id),
    path text NOT NULL,
    sha256 text NOT NULL,
    lineage jsonb NOT NULL,
    UNIQUE (name, sha256)
)""",
    """
CREATE TABLE public.datasets (
    sha256 text PRIMARY KEY CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    provenance jsonb NOT NULL,
    build_dir text NOT NULL,
    built_by text NOT NULL,
    published_at timestamptz
)""",
    f"""
CREATE TABLE public.training_runs (
    {ID},
    job text NOT NULL,
    submission jsonb NOT NULL,
    arbiter_run_id text,
    spec_sha256 text NOT NULL,
    status text NOT NULL CHECK (status IN ('submitted', 'running', 'suspended', 'succeeded',
        'failed', 'interrupted', 'registered')),
    transitions jsonb NOT NULL,
    adapter_id bigint REFERENCES public.adapters (id),
    submitted_by text NOT NULL,
    submitted_at timestamptz NOT NULL,
    finished_at timestamptz
)""",
    f"""
CREATE TABLE public.deployment_activations (
    {ID},
    deployment_id bigint NOT NULL REFERENCES public.deployments (id),
    served_model_name text NOT NULL,
    started_at timestamptz NOT NULL,
    ready_at timestamptz,
    ended_at timestamptz,
    verification jsonb
)""",
    f"""
CREATE TABLE public.run_events (
    id text PRIMARY KEY CHECK (id {ULID}),
    {RUN},
    seq integer NOT NULL CHECK (seq >= 1),
    type text NOT NULL,
    source text NOT NULL,
    time timestamptz NOT NULL,
    data jsonb NOT NULL,
    envelope bytea NOT NULL,
    prev_hash bytea NOT NULL CHECK (octet_length(prev_hash) = 32),
    hash bytea NOT NULL UNIQUE CHECK (octet_length(hash) = 32),
    UNIQUE (run_id, seq)
)""",
    f"""
CREATE TABLE public.agent_steps (
    {ID},
    {RUN},
    step integer NOT NULL CHECK (step >= 0),
    kind text NOT NULL,
    content_artifact {ARTIFACT},
    tokens integer,
    created_at timestamptz NOT NULL
)""",
    f"""
CREATE TABLE public.diagnoses (
    {ID},
    {RUN},
    step integer NOT NULL CHECK (step >= 0),
    label text NOT NULL,
    root_cause text,
    affected_component text,
    confidence double precision CHECK (confidence >= 0 AND confidence <= 1),
    evidence_refs text[] NOT NULL,
    is_pre_action boolean NOT NULL,
    is_final boolean NOT NULL,
    created_at timestamptz NOT NULL
)""",
    f"""
CREATE TABLE public.llm_calls (
    {ID},
    {RUN},
    step integer NOT NULL CHECK (step >= 0),
    {ACTIVATION},
    request_artifact {ARTIFACT} NOT NULL,
    response_artifact {ARTIFACT},
    logprobs_artifact {ARTIFACT},
    prompt_tokens integer,
    completion_tokens integer,
    ttft_ms integer,
    total_ms integer,
    finish_reason text,
    vllm_request_id text,
    span_id text
)""",
    f"""
CREATE TABLE public.llm_usage (
    {ID},
    "user" text NOT NULL,
    {ACTIVATION},
    prompt_tokens integer,
    completion_tokens integer,
    total_ms integer,
    created_at timestamptz NOT NULL
)""",
    f"""
CREATE TABLE public.tool_calls (
    {ID},
    {RUN},
    step integer NOT NULL CHECK (step >= 0),
    caller text NOT NULL,
    tool text NOT NULL,
    tool_version text NOT NULL,
    args jsonb NOT NULL,
    meta jsonb NOT NULL,
    valid boolean NOT NULL,
    validation_error text,
    created_at timestamptz NOT NULL
)""",
    f"""
CREATE TABLE public.policy_decisions (
    {ID},
    {TOOL_CALL},
    effect text NOT NULL,
    rule_id text,
    reason text,
    input_hash text NOT NULL,
    bundle_hash text NOT NULL,
    created_at timestamptz NOT NULL
)""",
    f"""
CREATE TABLE public.approvals (
    {ID},
    {TOOL_CALL},
    mode text NOT NULL,
    state text NOT NULL,
    decided_by text,
    decision text,
    comment text,
    requested_at timestamptz NOT NULL,
    decided_at timestamptz
)""",
    f"""
CREATE TABLE public.executions (
    {ID},
    {TOOL_CALL},
    node_alias text NOT NULL,
    instance_name text NOT NULL,
    argv jsonb NOT NULL,
    exit_code integer,
    status text NOT NULL,
    duration_ms bigint,
    stdout_artifact {ARTIFACT},
    stderr_artifact {ARTIFACT},
    truncated boolean NOT NULL,
    post_check jsonb
)""",
    f"""
CREATE TABLE public.probe_results (
    {ID},
    {RUN},
    phase text NOT NULL,
    probe text NOT NULL,
    params jsonb NOT NULL,
    passed boolean NOT NULL,
    detail jsonb NOT NULL,
    output_artifact {ARTIFACT},
    created_at timestamptz NOT NULL
)""",
    f"""
CREATE TABLE public.annotations (
    {ID},
    {RUN},
    author text NOT NULL,
    kind text NOT NULL,
    body text NOT NULL,
    created_at timestamptz NOT NULL
)""",
    f"""
CREATE TABLE public.evaluation_jobs (
    {ID},
    kind text NOT NULL,
    params jsonb NOT NULL,
    requested_by text NOT NULL,
    state text NOT NULL,
    created_at timestamptz NOT NULL,
    started_at timestamptz,
    finished_at timestamptz,
    error text
)""",
    f"""
CREATE TABLE public.evaluations (
    {ID},
    {RUN},
    evaluator_version text NOT NULL,
    taxonomy_version_id bigint NOT NULL REFERENCES public.taxonomy_versions (id),
    scores jsonb NOT NULL,
    created_at timestamptz NOT NULL
)""",
    f"""
CREATE TABLE public.contamination_flags (
    {ID},
    {RUN},
    adapter_id bigint NOT NULL REFERENCES public.adapters (id),
    scenario_template text NOT NULL,
    dataset_sha256 text NOT NULL REFERENCES public.datasets (sha256),
    evaluator_version text NOT NULL,
    created_at timestamptz NOT NULL
)""",
    f"""
CREATE TABLE public.eval_snapshots (
    {ID},
    params jsonb NOT NULL,
    content_hash text NOT NULL,
    manifest_artifact {ARTIFACT} NOT NULL,
    evaluator_version text NOT NULL,
    created_at timestamptz NOT NULL
)""",
    f"""
CREATE TABLE public.eval_outputs (
    {ID},
    kind text NOT NULL,
    snapshot_id bigint NOT NULL REFERENCES public.eval_snapshots (id),
    artifacts jsonb NOT NULL,
    created_at timestamptz NOT NULL
)""",
    f"""
CREATE TABLE public.api_keys (
    {ID},
    "user" text NOT NULL,
    key_hash text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL,
    revoked_at timestamptz
)""",
    *(f"CREATE INDEX {table}_run_id ON public.{table} (run_id)" for table in RUN_SCOPED[1:]),
    *(
        f"CREATE INDEX {table}_tool_call_id ON public.{table} (tool_call_id)"
        for table in BY_TOOL_CALL
    ),
    *(f'CREATE INDEX {table}_{column} ON public.{table} ("{column}")' for table, column in INDEXED),
)


def upgrade() -> None:
    """Create the tables."""
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Migrations are forward-only."""
    raise NotImplementedError("migrations are forward-only")
