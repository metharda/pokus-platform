"""A run's events and their hash chain.

Each event's hash is ``sha256(E || P)``: ``E`` is the RFC 8785 canonical
JSON of the event without its hash (``id``, ``run_id``, ``seq``, ``type``,
``source``, ``time``, ``data``, and ``prev_hash`` as lowercase hex), stored
as ``envelope``; ``P`` is the previous event's hash, 32 raw bytes; the
first event's ``P`` is the sha256 of the run ID's ASCII. The last hash is
the chain head the run's manifest records.

A verifier recomputes the chain from the stored envelopes, never from
``data`` (``jsonb`` normalizes numbers), checks that each envelope's fields
equal its row's columns, and compares the last hash with the head the
manifest recorded, which is how a removed last event is found.

Appends to one run, and the sealing of a run, take the run's
transaction-scoped advisory lock (:func:`lock_run`), in a READ COMMITTED
transaction, so a sealed run's chain head is its last event. A transaction
takes that lock before any lock on the run's row, as sealing does (lock,
then the row's UPDATE); the other order can deadlock with sealing. An event
cannot hold a NUL character (``jsonb`` and ``text`` refuse it), a subject
other than its run, nor a time whose UTC form is outside years 1 to 9999.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from potka_core.canonical import CanonicalJSONError, canonical_json
from potka_core.events import Event
from sqlalchemy import Connection, func, insert, select, text

from potka_db.tables import run_events

KEYS = frozenset({"id", "run_id", "seq", "type", "source", "time", "data", "prev_hash"})
# Rows whose envelope's fields differ from their columns (compared as jsonb;
# a missing or null field differs too).
MISMATCHED_ROWS = text(
    """
SELECT seq FROM (
    SELECT *, convert_from(envelope, 'UTF8')::jsonb AS e FROM public.run_events
    WHERE run_id = :run_id
) rows
WHERE (e ->> 'id') IS DISTINCT FROM id
   OR (e ->> 'run_id') IS DISTINCT FROM run_id
   OR (e -> 'seq') IS DISTINCT FROM to_jsonb(seq)
   OR (e ->> 'type') IS DISTINCT FROM type
   OR (e ->> 'source') IS DISTINCT FROM source
   OR (e ->> 'time')::timestamptz IS DISTINCT FROM time
   OR (e -> 'data') IS DISTINCT FROM data
   OR (e ->> 'prev_hash') IS DISTINCT FROM encode(prev_hash, 'hex')
ORDER BY seq
"""
)


class ChainError(ValueError):
    """A run's event chain does not verify."""


class EventError(ValueError):
    """An event that cannot be stored: a NUL character, or a subject other than its run."""


def first_prev_hash(run_id: str) -> bytes:
    """The ``P`` of a run's first event.

    Args:
        run_id: The run's ID.

    Returns:
        The sha256 of the run ID's ASCII.
    """
    return hashlib.sha256(run_id.encode("ascii")).digest()


def envelope(run_id: str, seq: int, event: Event, prev_hash: bytes) -> bytes:
    """The bytes an event's hash covers (``E``).

    Args:
        run_id: The run.
        seq: The event's position in the run, from 1.
        event: The event.
        prev_hash: ``P``, 32 bytes.

    Returns:
        The canonical JSON.
    """
    return canonical_json(
        {
            "id": event.id,
            "run_id": run_id,
            "seq": seq,
            "type": event.type,
            "source": event.source,
            "time": _utc(event.time),
            "data": event.data,
            "prev_hash": prev_hash.hex(),
        }
    )


def _utc(time: datetime) -> str:
    return time.astimezone(UTC).isoformat().replace("+00:00", "Z")


def event_hash(envelope_bytes: bytes, prev_hash: bytes) -> bytes:
    """``sha256(E || P)``.

    Args:
        envelope_bytes: ``E``.
        prev_hash: ``P``, 32 bytes.

    Returns:
        The hash, 32 bytes.
    """
    return hashlib.sha256(envelope_bytes + prev_hash).digest()


def _has_nul(value: Any) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_has_nul(k) or _has_nul(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_nul(item) for item in value)
    return False


def lock_run(conn: Connection, run_id: str) -> None:
    """Take RUN_ID's advisory lock until the transaction ends.

    Take it before any lock on the run's row (an UPDATE of it), as
    :func:`potka_db.sealing.seal` does.

    Args:
        conn: A connection in a READ COMMITTED transaction.
        run_id: The run.

    Raises:
        ValueError: The connection is in autocommit mode, which would release
            the lock with its statement, or its isolation level is not READ
            COMMITTED, under which a waiter would not see its predecessor's
            commit.
    """
    autocommit = getattr(conn.connection.driver_connection, "autocommit", False)
    if autocommit or conn.get_isolation_level() != "READ COMMITTED":
        raise ValueError("a run's events are written in a READ COMMITTED transaction")
    conn.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(run_id, 0))))


def head(conn: Connection, run_id: str) -> bytes | None:
    """The hash of RUN_ID's last event.

    Args:
        conn: A connection that may read ``run_events``.
        run_id: The run.

    Returns:
        The hash, or None for a run without events.
    """
    return conn.execute(
        select(run_events.c.hash)
        .where(run_events.c.run_id == run_id)
        .order_by(run_events.c.seq.desc())
        .limit(1)
    ).scalar()


def append(conn: Connection, run_id: str, event: Event) -> bytes:
    """Append EVENT to RUN_ID's chain, in the caller's transaction.

    Args:
        conn: A connection that may read and insert ``run_events``, in a
            READ COMMITTED transaction the caller commits.
        run_id: The run.
        event: The event.

    Returns:
        The event's hash, the run's new chain head.

    Raises:
        EventError: The event holds a NUL character, a subject other than
            RUN_ID, or a time outside years 1 to 9999 in UTC; nothing was
            written.
        ValueError: The connection is not in a READ COMMITTED transaction
            (:func:`lock_run`).
    """
    if _has_nul([event.id, event.type, event.source, event.data]):
        raise EventError("an event cannot hold a NUL character")
    if event.subject is not None and event.subject != run_id:
        raise EventError("an event's subject is its run")
    try:
        _utc(event.time)
    except OverflowError:
        raise EventError("an event's time is outside years 1 to 9999 in UTC") from None
    lock_run(conn, run_id)
    last = conn.execute(
        select(run_events.c.seq, run_events.c.hash)
        .where(run_events.c.run_id == run_id)
        .order_by(run_events.c.seq.desc())
        .limit(1)
    ).first()
    seq, prev_hash = (1, first_prev_hash(run_id)) if last is None else (last.seq + 1, last.hash)
    body = envelope(run_id, seq, event, prev_hash)
    digest = event_hash(body, prev_hash)
    conn.execute(
        insert(run_events).values(
            id=event.id,
            run_id=run_id,
            seq=seq,
            type=event.type,
            source=event.source,
            time=event.time,
            data=event.data,
            envelope=body,
            prev_hash=prev_hash,
            hash=digest,
        )
    )
    return digest


def _well_formed(body: bytes) -> bool:
    try:
        fields = json.loads(body)
        canonical = canonical_json(fields)
    except (ValueError, RecursionError, CanonicalJSONError):
        return False
    if canonical != body or not isinstance(fields, dict) or set(fields) != KEYS:
        return False
    if _has_nul(fields):
        return False
    strings = ("id", "run_id", "type", "source", "time", "prev_hash")
    if not all(isinstance(fields[key], str) for key in strings):
        return False
    if type(fields["seq"]) is not int or not isinstance(fields["data"], dict):
        return False
    try:
        time = datetime.fromisoformat(fields["time"])
    except ValueError:
        return False
    # Only the form append writes: UTC, with Z.
    return time.tzinfo is not None and _utc(time) == fields["time"]


def verify(conn: Connection, run_id: str, expected_head: bytes | None = None) -> bytes:
    """Verify RUN_ID's chain from its stored envelopes.

    Args:
        conn: A connection that may read ``run_events``.
        run_id: The run.
        expected_head: The chain head the run's manifest recorded, which
            the chain head must equal (None: not compared).

    Returns:
        The chain head: the last event's hash, or for a run without events
        the sha256 of its ID, as sealing records it.

    Raises:
        ChainError: A gap in the positions, a previous hash or hash that
            does not follow, an envelope that is not the canonical JSON of
            an event's eight fields, an envelope whose fields differ from
            its row, or a head other than EXPECTED_HEAD.
    """
    rows = conn.execute(
        select(run_events.c.seq, run_events.c.envelope, run_events.c.prev_hash, run_events.c.hash)
        .where(run_events.c.run_id == run_id)
        .order_by(run_events.c.seq)
    ).all()
    prev_hash = first_prev_hash(run_id)
    for expected, row in enumerate(rows, 1):
        if row.seq != expected:
            raise ChainError(f"event {expected} is missing")
        if row.prev_hash != prev_hash:
            raise ChainError(f"event {expected} does not follow its predecessor")
        if event_hash(row.envelope, prev_hash) != row.hash:
            raise ChainError(f"event {expected}'s hash is not its envelope's")
        if not _well_formed(row.envelope):
            raise ChainError(f"event {expected}'s envelope is not an event's canonical JSON")
        prev_hash = row.hash
    mismatched = conn.execute(MISMATCHED_ROWS, {"run_id": run_id}).scalars().first()
    if mismatched is not None:
        raise ChainError(f"event {mismatched}'s envelope differs from its row")
    end: bytes = rows[-1].hash if rows else first_prev_hash(run_id)
    if expected_head is not None and end != expected_head:
        raise ChainError("the chain does not end at the recorded head")
    return end
