"""Sealing a run: the one path by which a run's scenario reaches ``public``.

In the caller's transaction, :func:`seal` marks the run sealed (its phase,
time, manifest, signature and chain head) and copies its scenario version
and instance from ``restricted.run_assignments`` into
``public.sealed_run_scenarios``. The database checks at commit that the
two agree.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Connection, insert, literal, select, update

from potka_db.tables import run_assignments, runs, sealed_run_scenarios


class SealingError(RuntimeError):
    """The run cannot be sealed: it does not exist, is sealed already, or has no assignment."""


def seal(
    conn: Connection,
    run_id: str,
    sealed_at: datetime,
    sealed_ns: int,
    manifest_artifact: str,
    signature_artifact: str,
    chain_head: bytes,
) -> None:
    """Seal RUN_ID and declassify its scenario, in the caller's transaction.

    On :class:`SealingError` the caller rolls the transaction back: the run
    may already be marked sealed in it, and the commit would be refused.

    Args:
        conn: A connection of the controller, in a transaction the caller
            commits.
        run_id: The run.
        sealed_at: The sealing time (UTC); also the time point ``t_sealed``.
        sealed_ns: The sealing time on the monotonic clock (``t_sealed_ns``).
        manifest_artifact: The sha256 of the run's manifest artifact.
        signature_artifact: The sha256 of the manifest's signature artifact.
        chain_head: The hash of the run's last event (32 bytes).

    Raises:
        SealingError: The run is unknown or sealed already, or has no
            assignment.
    """
    marked = conn.execute(
        update(runs)
        .where(runs.c.id == run_id, runs.c.sealed_at.is_(None))
        .values(
            phase="sealed",
            sealed_at=sealed_at,
            t_sealed=sealed_at,
            t_sealed_ns=sealed_ns,
            manifest_artifact=manifest_artifact,
            signature_artifact=signature_artifact,
            chain_head=chain_head,
        )
    )
    if marked.rowcount != 1:
        raise SealingError("the run is unknown or sealed already")
    declassified = conn.execute(
        insert(sealed_run_scenarios)
        .from_select(
            ["run_id", "scenario_version_id", "instance_index", "sealed_at"],
            select(
                run_assignments.c.run_id,
                run_assignments.c.scenario_version_id,
                run_assignments.c.instance_index,
                literal(sealed_at, sealed_run_scenarios.c.sealed_at.type),
            ).where(run_assignments.c.run_id == run_id),
        )
        .returning(sealed_run_scenarios.c.run_id)
    ).all()
    if len(declassified) != 1:
        raise SealingError("the run has no assignment")
