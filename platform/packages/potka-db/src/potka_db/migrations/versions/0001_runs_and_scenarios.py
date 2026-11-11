"""The run and its scenario.

Lab sessions, catalog and scenario versions, taxonomies, models,
deployments, agent configurations, artifacts, campaigns, runs, the
declassified scenarios of sealed runs, and in ``restricted`` the run
assignments and the scenario payloads.

``public.runs`` has no scenario field: the scenario version, the instance
and the scenario seed are in ``restricted.run_assignments``; the scenario
version and the instance reach ``public`` only in ``sealed_run_scenarios``,
when the run is sealed. A sealed run's row is complete (phase, manifest,
signature, chain head), and each time point has both its clocks.

Revision ID: 0001
Revises: -
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ULID = "~ '^[0-7][0-9A-HJKMNP-TV-Z]{25}$'"
SHA256 = "~ '^[0-9a-f]{64}$'"
PHASES = (
    "'queued', 'provisioning', 'preconditions', 'baseline', 'setup', 'injecting', 'confirming',"
    " 'settling', 'evidence', 'agent', 'verifying', 'collecting', 'teardown', 'sealed'"
)
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
TIMES = "".join(f"\n    t_{p} timestamptz,\n    t_{p}_ns bigint," for p in TIME_POINTS)
TIME_PAIRS = "\n        AND ".join(f"(t_{p} IS NULL) = (t_{p}_ns IS NULL)" for p in TIME_POINTS)

STATEMENTS = (
    """
CREATE TABLE public.lab_sessions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    boot_id text NOT NULL UNIQUE,
    started_at timestamptz NOT NULL,
    fingerprint_hash text,
    fingerprint jsonb,
    preflight_outcome text
        CHECK (preflight_outcome IN ('supported', 'degraded', 'unsupported', 'needs_action')),
    errors jsonb NOT NULL
)""",
    """
CREATE TABLE public.catalog_versions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ref text NOT NULL,
    commit text NOT NULL CHECK (commit ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'),
    imported_at timestamptz NOT NULL,
    content_hash text NOT NULL,
    UNIQUE (ref, commit)
)""",
    """
CREATE TABLE public.scenario_versions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    catalog_version_id bigint NOT NULL REFERENCES public.catalog_versions (id),
    scenario_id text NOT NULL,
    version text NOT NULL,
    title text NOT NULL,
    risk_level text NOT NULL,
    isolation text NOT NULL,
    blueprint text NOT NULL,
    split text NOT NULL CHECK (split IN ('train', 'dev', 'test')),
    tags text[] NOT NULL,
    public_meta jsonb NOT NULL,
    UNIQUE (catalog_version_id, scenario_id, version)
)""",
    """
CREATE TABLE public.taxonomy_versions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    version text NOT NULL UNIQUE,
    labels jsonb NOT NULL,
    hash text NOT NULL
)""",
    f"""
CREATE TABLE public.artifacts (
    sha256 text PRIMARY KEY CHECK (sha256 {SHA256}),
    size bigint NOT NULL CHECK (size >= 0),
    media_type text NOT NULL,
    compressed boolean NOT NULL,
    created_at timestamptz NOT NULL,
    created_by text NOT NULL
)""",
    """
CREATE TABLE public.models (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    hf_repo text NOT NULL,
    revision text NOT NULL,
    files jsonb NOT NULL,
    quantization text,
    license text,
    registered_at timestamptz NOT NULL,
    UNIQUE NULLS NOT DISTINCT (hf_repo, revision, quantization)
)""",
    """
CREATE TABLE public.deployments (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model_id bigint NOT NULL REFERENCES public.models (id),
    adapter_ids bigint[] NOT NULL,
    profile text NOT NULL,
    serving_args jsonb NOT NULL,
    vllm_image_digest text NOT NULL CHECK (vllm_image_digest ~ '^sha256:[0-9a-f]{64}$'),
    spec_hash text NOT NULL UNIQUE
)""",
    """
CREATE TABLE public.agent_configs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL,
    version text NOT NULL,
    prompt_template_hash text NOT NULL,
    prompt_artifact text NOT NULL REFERENCES public.artifacts (sha256),
    mode text NOT NULL,
    budgets jsonb NOT NULL,
    sampling jsonb NOT NULL,
    tool_registry_version text NOT NULL,
    tool_subset text[] NOT NULL,
    evidence_profile jsonb NOT NULL,
    policy_bundle_hash text NOT NULL,
    hash text NOT NULL,
    UNIQUE (name, version)
)""",
    f"""
CREATE TABLE public.campaigns (
    id text PRIMARY KEY CHECK (id {ULID}),
    name text NOT NULL,
    submitter text NOT NULL,
    spec jsonb NOT NULL,
    spec_hash text NOT NULL,
    official boolean NOT NULL,
    official_reasons jsonb NOT NULL,
    attestation_id text CHECK (attestation_id {ULID}),
    state text NOT NULL,
    created_at timestamptz NOT NULL,
    platform_version text NOT NULL,
    catalog_version_id bigint NOT NULL REFERENCES public.catalog_versions (id),
    deployment_id bigint NOT NULL REFERENCES public.deployments (id),
    agent_config_id bigint NOT NULL REFERENCES public.agent_configs (id),
    approval_mode text NOT NULL
        CHECK (approval_mode IN ('deny_all', 'approve_all', 'oracle', 'human')),
    run_mode text NOT NULL CHECK (run_mode IN ('throughput', 'measurement')),
    concurrency integer NOT NULL CHECK (concurrency >= 1),
    seed bigint NOT NULL
)""",
    f"""
CREATE TABLE public.runs (
    id text PRIMARY KEY CHECK (id {ULID}),
    campaign_id text NOT NULL REFERENCES public.campaigns (id),
    repetition integer NOT NULL CHECK (repetition >= 0),
    seed_sampling bigint NOT NULL,
    session_id bigint REFERENCES public.lab_sessions (id),
    phase text NOT NULL CHECK (phase IN ({PHASES})),
    validity text CHECK (validity IN ('valid', 'invalid')),
    invalid_reason text,
    outcome text CHECK (
        outcome IN ('diagnosed', 'abstained', 'protocol_failure', 'finished')
        OR outcome ~ '^budget_exhausted:[a-z][a-z_]*$'
    ),
    official boolean NOT NULL,
    retry_of text REFERENCES public.runs (id),{TIMES}
    trace_id text,
    manifest_artifact text REFERENCES public.artifacts (sha256),
    signature_artifact text REFERENCES public.artifacts (sha256),
    chain_head bytea CHECK (octet_length(chain_head) = 32),
    sealed_at timestamptz,
    CONSTRAINT runs_sealed_phase CHECK ((phase = 'sealed') = (sealed_at IS NOT NULL)),
    CONSTRAINT runs_sealed_record CHECK (sealed_at IS NULL OR (manifest_artifact IS NOT NULL
        AND signature_artifact IS NOT NULL AND chain_head IS NOT NULL)),
    CONSTRAINT runs_invalid_reason
        CHECK ((validity IS NOT DISTINCT FROM 'invalid') = (invalid_reason IS NOT NULL)),
    CONSTRAINT runs_time_points CHECK (
        {TIME_PAIRS}
    )
)""",
    "CREATE INDEX runs_campaign_id ON public.runs (campaign_id)",
    "CREATE INDEX runs_phase ON public.runs (phase)",
    "CREATE INDEX runs_session_id ON public.runs (session_id)",
    "CREATE INDEX runs_retry_of ON public.runs (retry_of)",
    """
CREATE TABLE public.sealed_run_scenarios (
    run_id text PRIMARY KEY REFERENCES public.runs (id),
    scenario_version_id bigint NOT NULL REFERENCES public.scenario_versions (id),
    instance_index integer NOT NULL CHECK (instance_index >= 0),
    sealed_at timestamptz NOT NULL
)""",
    "CREATE INDEX sealed_run_scenarios_scenario_version_id"
    " ON public.sealed_run_scenarios (scenario_version_id)",
    """
CREATE TABLE restricted.run_assignments (
    run_id text PRIMARY KEY REFERENCES public.runs (id),
    scenario_version_id bigint NOT NULL REFERENCES public.scenario_versions (id),
    instance_index integer NOT NULL CHECK (instance_index >= 0),
    seed_scenario bigint NOT NULL,
    parameters jsonb NOT NULL,
    gt_hash text NOT NULL
)""",
    "CREATE INDEX run_assignments_scenario_version_id"
    " ON restricted.run_assignments (scenario_version_id)",
    """
CREATE TABLE restricted.scenario_payloads (
    scenario_version_id bigint PRIMARY KEY REFERENCES public.scenario_versions (id),
    scenario_doc jsonb NOT NULL,
    ground_truth_doc jsonb NOT NULL
)""",
)


def upgrade() -> None:
    """Create the tables."""
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Migrations are forward-only."""
    raise NotImplementedError("migrations are forward-only")
