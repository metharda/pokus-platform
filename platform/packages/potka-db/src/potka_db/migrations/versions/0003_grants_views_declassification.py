"""Grants, the researchers' views, and the declassification of a sealed run's scenario.

The service roles get what their services use, through the group roles of
their profiles: the controller reads and writes the records and the
restricted schema; the inference broker inserts model calls and usage; the
sandbox operator inserts tool calls, policy decisions and executions; the
restricted reader reads everything and writes evaluations. The
restricted operators get nothing yet. Nobody may delete.

The reader and Grafana roles read only the two views, which read only
``public``: a run's scenario and instance come from
``sealed_run_scenarios``, so an unsealed run shows none of them, nor its
repetition. The views are security barriers: a reader's condition is not
evaluated on rows the view does not return.

A run's scenario reaches ``public`` only when the run is sealed: a row of
``sealed_run_scenarios`` must equal the run's assignment and carry the
run's ``sealed_at``, and a sealed run must have its row; both are checked
when the transaction commits.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

CONTROLLER = "potka_controller"
BROKER = "potka_inference_broker"
SANDBOX = "potka_sandbox_operator"
READER = "potka_restricted_reader"
RESEARCHERS = ("potka_reader", "potka_grafana")
# Every table of public (not alembic_version, not the views below).
PUBLIC_TABLES = (
    "lab_sessions",
    "catalog_versions",
    "scenario_versions",
    "taxonomy_versions",
    "artifacts",
    "models",
    "deployments",
    "agent_configs",
    "campaigns",
    "runs",
    "sealed_run_scenarios",
    "blueprints",
    "derived_images",
    "adapters",
    "datasets",
    "training_runs",
    "deployment_activations",
    "run_events",
    "agent_steps",
    "diagnoses",
    "llm_calls",
    "llm_usage",
    "tool_calls",
    "policy_decisions",
    "approvals",
    "executions",
    "probe_results",
    "annotations",
    "evaluation_jobs",
    "evaluations",
    "contamination_flags",
    "eval_snapshots",
    "eval_outputs",
    "api_keys",
)
ALL_PUBLIC = ", ".join(f"public.{table}" for table in PUBLIC_TABLES)
RESTRICTED_TABLES = "restricted.run_assignments, restricted.scenario_payloads"
STATE_TABLES = (
    "runs",
    "campaigns",
    "approvals",
    "training_runs",
    "deployment_activations",
    "evaluation_jobs",
    "api_keys",
)

STATEMENTS = (
    f"GRANT SELECT, INSERT ON {ALL_PUBLIC} TO {CONTROLLER}",
    f"GRANT UPDATE ON {', '.join(f'public.{t}' for t in STATE_TABLES)} TO {CONTROLLER}",
    f"GRANT USAGE ON SCHEMA restricted TO {CONTROLLER}",
    f"GRANT SELECT, INSERT ON {RESTRICTED_TABLES} TO {CONTROLLER}",
    f"GRANT INSERT ON public.llm_calls, public.llm_usage TO {BROKER}",
    f"GRANT INSERT ON public.tool_calls, public.policy_decisions, public.executions TO {SANDBOX}",
    f"GRANT SELECT ON {ALL_PUBLIC} TO {READER}",
    f"GRANT USAGE ON SCHEMA restricted TO {READER}",
    f"GRANT SELECT ON {RESTRICTED_TABLES} TO {READER}",
    f"GRANT INSERT ON public.evaluations, public.eval_snapshots, public.contamination_flags"
    f" TO {READER}",
    f"GRANT INSERT, UPDATE ON public.evaluation_jobs TO {READER}",
    """
CREATE VIEW public.run_summaries WITH (security_barrier) AS
SELECT
    r.id AS run_id,
    r.campaign_id,
    r.phase,
    r.validity,
    r.invalid_reason,
    r.outcome,
    r.official,
    CASE WHEN r.sealed_at IS NOT NULL THEN r.repetition END AS repetition,
    r.t_queued,
    r.t_agent_start,
    r.t_agent_end,
    r.sealed_at,
    s.scenario_version_id,
    v.scenario_id,
    v.version AS scenario_version,
    s.instance_index
FROM public.runs r
LEFT JOIN public.sealed_run_scenarios s ON s.run_id = r.id AND r.sealed_at IS NOT NULL
LEFT JOIN public.scenario_versions v ON v.id = s.scenario_version_id""",
    """
CREATE VIEW public.campaign_summaries WITH (security_barrier) AS
SELECT
    c.id AS campaign_id,
    c.name,
    c.submitter,
    c.official,
    c.state,
    c.created_at,
    c.platform_version,
    c.approval_mode,
    c.run_mode,
    (SELECT count(*) FROM public.runs r WHERE r.campaign_id = c.id) AS runs,
    (SELECT count(*) FROM public.runs r WHERE r.campaign_id = c.id AND r.sealed_at IS NOT NULL)
        AS sealed_runs
FROM public.campaigns c""",
    *(
        f"GRANT SELECT ON public.run_summaries, public.campaign_summaries TO {role}"
        for role in RESEARCHERS
    ),
    """
CREATE FUNCTION restricted.check_declassification() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM public.runs r
        JOIN restricted.run_assignments a ON a.run_id = r.id
        WHERE r.id = NEW.run_id
          AND r.sealed_at IS NOT NULL
          AND r.sealed_at = NEW.sealed_at
          AND a.scenario_version_id = NEW.scenario_version_id
          AND a.instance_index = NEW.instance_index
    ) THEN
        RAISE EXCEPTION 'a declassified scenario must be its sealed run''s assignment'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$""",
    "REVOKE ALL ON FUNCTION restricted.check_declassification() FROM PUBLIC",
    """
CREATE CONSTRAINT TRIGGER sealed_run_scenarios_declassification
AFTER INSERT ON public.sealed_run_scenarios
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION restricted.check_declassification()""",
    """
CREATE FUNCTION public.check_sealing() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.sealed_at IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.sealed_run_scenarios s
        WHERE s.run_id = NEW.id AND s.sealed_at = NEW.sealed_at
    ) THEN
        RAISE EXCEPTION 'a sealed run must have its declassified scenario'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$""",
    """
CREATE CONSTRAINT TRIGGER runs_sealing
AFTER INSERT OR UPDATE OF sealed_at ON public.runs
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION public.check_sealing()""",
)


def upgrade() -> None:
    """Grant, create the views and the declassification checks."""
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Migrations are forward-only."""
    raise NotImplementedError("migrations are forward-only")
