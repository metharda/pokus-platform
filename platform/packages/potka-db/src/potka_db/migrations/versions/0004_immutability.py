"""Sealed runs cannot change; insert-only tables never change.

Once a run is sealed, its row, the rows of its run-scoped tables and those
of its tool calls cannot be updated or deleted, and its record tables take
no new row (annotations, evaluations and contamination flags stay open).
The insert-only tables refuse every update and delete, and no table that
holds runs' records may be truncated.

Each check reads whether the run is sealed when its trigger runs, so a row
written before a concurrent sealing committed is not refused (nor, under
REPEATABLE READ, one written in a snapshot taken before it): the
controller seals a run only after every other writer of the run is done,
and appends and sealing take the run's event lock.

The checks run as the role that writes (no function here is SECURITY
DEFINER), so the inference broker and the sandbox operator, which insert
into run-scoped tables, read the two columns of ``runs`` that say whether a
run is sealed, and the sandbox operator the two columns of ``tool_calls``
that name a call's run.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# Tables whose rows name their run (run_id), and those that name a tool call.
RUN_SCOPED = (
    "public.run_events",
    "public.agent_steps",
    "public.diagnoses",
    "public.llm_calls",
    "public.tool_calls",
    "public.probe_results",
    "public.annotations",
    "public.evaluations",
    "public.contamination_flags",
    "public.sealed_run_scenarios",
    "restricted.run_assignments",
)
BY_TOOL_CALL = ("public.policy_decisions", "public.approvals", "public.executions")
# The record tables that take no new row once their run is sealed.
CLOSED_AT_SEALING = (
    "public.run_events",
    "public.agent_steps",
    "public.diagnoses",
    "public.llm_calls",
    "public.tool_calls",
    "public.probe_results",
    "restricted.run_assignments",
)
CLOSED_BY_TOOL_CALL = BY_TOOL_CALL
INSERT_ONLY = (
    "public.run_events",
    "public.llm_calls",
    "public.tool_calls",
    "public.executions",
    "public.probe_results",
    "public.diagnoses",
    "public.policy_decisions",
    "public.sealed_run_scenarios",
)


def _name(table: str) -> str:
    return table.split(".")[1]


STATEMENTS = (
    """
CREATE FUNCTION public.run_is_sealed(run text) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog
RETURN EXISTS (SELECT 1 FROM public.runs r WHERE r.id = run AND r.sealed_at IS NOT NULL)""",
    """
CREATE FUNCTION public.run_of_tool_call(call bigint) RETURNS text
LANGUAGE sql STABLE SET search_path = pg_catalog
RETURN (SELECT t.run_id FROM public.tool_calls t WHERE t.id = call)""",
    """
CREATE FUNCTION public.refuse_change_of_a_sealed_run() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
    runs text[] := ARRAY[]::text[];
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        runs := runs || CASE WHEN TG_ARGV[0] = 'tool_call'
            THEN public.run_of_tool_call((to_jsonb(OLD) ->> 'tool_call_id')::bigint)
            ELSE to_jsonb(OLD) ->> 'run_id' END;
    END IF;
    IF TG_OP IN ('UPDATE', 'INSERT') THEN
        runs := runs || CASE WHEN TG_ARGV[0] = 'tool_call'
            THEN public.run_of_tool_call((to_jsonb(NEW) ->> 'tool_call_id')::bigint)
            ELSE to_jsonb(NEW) ->> 'run_id' END;
    END IF;
    IF EXISTS (SELECT 1 FROM unnest(runs) AS run WHERE public.run_is_sealed(run)) THEN
        RAISE EXCEPTION 'the records of a sealed run cannot change (%.%)',
            TG_TABLE_SCHEMA, TG_TABLE_NAME USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$""",
    """
CREATE FUNCTION public.refuse_change_of_a_sealed_run_row() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF OLD.sealed_at IS NOT NULL THEN
        RAISE EXCEPTION 'a sealed run cannot change'
            USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$""",
    """
CREATE FUNCTION public.refuse_change_of_an_insert_only_table() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION '%.% is insert-only', TG_TABLE_SCHEMA, TG_TABLE_NAME
        USING ERRCODE = 'object_not_in_prerequisite_state';
END
$$""",
    """
CREATE FUNCTION public.refuse_truncation_of_runs_records() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION '%.% holds runs'' records and cannot be truncated',
        TG_TABLE_SCHEMA, TG_TABLE_NAME USING ERRCODE = 'object_not_in_prerequisite_state';
END
$$""",
    """
CREATE TRIGGER runs_sealed BEFORE UPDATE OR DELETE ON public.runs
FOR EACH ROW EXECUTE FUNCTION public.refuse_change_of_a_sealed_run_row()""",
    *(
        f"CREATE TRIGGER {_name(t)}_sealed_change BEFORE UPDATE OR DELETE ON {t}"
        " FOR EACH ROW EXECUTE FUNCTION public.refuse_change_of_a_sealed_run('run')"
        for t in RUN_SCOPED
    ),
    *(
        f"CREATE TRIGGER {_name(t)}_sealed_change BEFORE UPDATE OR DELETE ON {t}"
        " FOR EACH ROW EXECUTE FUNCTION public.refuse_change_of_a_sealed_run('tool_call')"
        for t in BY_TOOL_CALL
    ),
    *(
        f"CREATE TRIGGER {_name(t)}_sealed_insert BEFORE INSERT ON {t}"
        " FOR EACH ROW EXECUTE FUNCTION public.refuse_change_of_a_sealed_run('run')"
        for t in CLOSED_AT_SEALING
    ),
    *(
        f"CREATE TRIGGER {_name(t)}_sealed_insert BEFORE INSERT ON {t}"
        " FOR EACH ROW EXECUTE FUNCTION public.refuse_change_of_a_sealed_run('tool_call')"
        for t in CLOSED_BY_TOOL_CALL
    ),
    *(
        f"CREATE TRIGGER {_name(t)}_insert_only BEFORE UPDATE OR DELETE ON {t}"
        " FOR EACH ROW EXECUTE FUNCTION public.refuse_change_of_an_insert_only_table()"
        for t in INSERT_ONLY
    ),
    *(
        f"CREATE TRIGGER {_name(t)}_no_truncate BEFORE TRUNCATE ON {t}"
        " FOR EACH STATEMENT EXECUTE FUNCTION public.refuse_truncation_of_runs_records()"
        for t in ("public.runs", *RUN_SCOPED, *BY_TOOL_CALL)
    ),
    "GRANT SELECT (id, sealed_at) ON public.runs TO potka_inference_broker, potka_sandbox_operator",
    "GRANT SELECT (id, run_id) ON public.tool_calls TO potka_sandbox_operator",
)


def upgrade() -> None:
    """Create the checks and the column grants they need."""
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Migrations are forward-only."""
    raise NotImplementedError("migrations are forward-only")
