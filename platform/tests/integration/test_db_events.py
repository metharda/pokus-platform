"""A run's event chain: sha256(E || P) from the stored envelopes; every change is found."""

from __future__ import annotations

import hashlib
import itertools
import json
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from potka_core.canonical import canonical_json
from potka_core.events import Event
from potka_db.connection import engine
from potka_db.events import (
    ChainError,
    EventError,
    append,
    envelope,
    event_hash,
    first_prev_hash,
    verify,
)
from potka_db.sealing import SealingError, seal
from potka_testing.contract import Database
from potka_testing.records import ARTIFACT, CAMPAIGN, RUN, assign, insert_run, seed
from sqlalchemy import Connection, text

TIME = datetime(2026, 11, 11, 12, 0, 1, 250000, tzinfo=UTC)
COUNTER = itertools.count(1)
RUN2 = "01HZY00000000000000000RN02"


def event(data: dict[str, Any], number: int = 1) -> Event:
    return Event(
        id=f"01HZY000000000000000EV{number:04d}",
        source="/potka/orch",
        type="potka.orch.phase-history",
        time=TIME,
        data=data,
    )


@pytest.fixture
def orch(migrated_database: Database) -> Iterator[Connection]:
    with migrated_database.connect("potka_migrator") as conn:
        seed(conn)
        insert_run(conn)
    params = {k: str(v) for k, v in migrated_database.params("potka_orch").items()}
    with engine(params).connect() as conn:
        yield conn


def test_the_envelope_and_hash_are_the_defined_bytes() -> None:
    body = envelope(RUN, 1, event({"phase": "queued"}), first_prev_hash(RUN))
    prev = hashlib.sha256(RUN.encode("ascii")).digest()
    assert body == (
        b'{"data":{"phase":"queued"},"id":"01HZY000000000000000EV0001","prev_hash":"'
        + prev.hex().encode()
        + b'","run_id":"01HZY00000000000000000RN01","seq":1,"source":"/potka/orch",'
        b'"time":"2026-11-11T12:00:01.250000Z","type":"potka.orch.phase-history"}'
    )
    assert event_hash(body, prev) == hashlib.sha256(body + prev).digest()


def test_appended_events_form_the_chain_and_verify(orch: Connection) -> None:
    heads = [append(orch, RUN, event({"step": n}, n)) for n in (1, 2, 3)]
    orch.commit()
    rows = orch.execute(text("SELECT seq, prev_hash, hash FROM run_events ORDER BY seq")).all()
    assert [r.seq for r in rows] == [1, 2, 3]
    assert rows[0].prev_hash == first_prev_hash(RUN)
    assert [r.prev_hash for r in rows[1:]] == heads[:2]
    assert [r.hash for r in rows] == heads
    assert verify(orch, RUN) == heads[-1]
    assert verify(orch, RUN2) == first_prev_hash(RUN2)  # a run without events


def test_numbers_jsonb_rewrites_still_verify_from_the_envelope(orch: Connection) -> None:
    head = append(orch, RUN, event({"big": 1e30, "half": 0.5, "count": 100}))
    orch.commit()
    stored = orch.execute(text("SELECT data::text FROM run_events")).scalar_one()
    assert "1000000000000000000000000000000" in stored
    assert verify(orch, RUN) == head


TAMPERING = {
    "envelope byte": (
        "UPDATE run_events SET envelope = overlay(envelope placing 'X' from 3) WHERE seq = 2",
        "hash is not its envelope's",
    ),
    "data": ("UPDATE run_events SET data = '{\"step\": 9}' WHERE seq = 2", "differs from its row"),
    "type": (
        "UPDATE run_events SET type = 'potka.orch.other' WHERE seq = 2",
        "differs from its row",
    ),
    "id": ("UPDATE run_events SET id = '01HZY000000000000000EV0099' WHERE seq = 2", "differs"),
    "source": ("UPDATE run_events SET source = '/potka/gw' WHERE seq = 2", "differs from its row"),
    "time": ("UPDATE run_events SET time = time + interval '1 s' WHERE seq = 2", "differs"),
    "prev hash": ("UPDATE run_events SET prev_hash = hash WHERE seq = 2", "does not follow"),
    "hash": ("UPDATE run_events SET hash = sha256('other') WHERE seq = 3", "hash is not"),
    "gap": ("DELETE FROM run_events WHERE seq = 2", "event 2 is missing"),
    "first": ("UPDATE run_events SET prev_hash = sha256('other') WHERE seq = 1", "does not follow"),
    "reordered": (
        "UPDATE run_events SET seq = 99 WHERE seq = 2;"
        " UPDATE run_events SET seq = 2 WHERE seq = 3;"
        " UPDATE run_events SET seq = 3 WHERE seq = 99",
        "does not follow",
    ),
    "moved away": (
        "UPDATE run_events SET run_id = '01HZY00000000000000000RN02' WHERE seq = 3",
        "recorded head",
    ),
}


@pytest.mark.parametrize("change", sorted(TAMPERING))
def test_every_change_to_a_stored_event_is_found(
    orch: Connection, migrated_database: Database, change: str
) -> None:
    for n in (1, 2, 3):
        append(orch, RUN, event({"step": n}, n))
    orch.commit()
    head = verify(orch, RUN)
    statement, message = TAMPERING[change]
    with migrated_database.server.connect(migrated_database.name) as conn:
        conn.execute("SET session_replication_role = replica")  # past the insert-only trigger
        conn.execute(statement)
    with pytest.raises(ChainError, match=message):
        verify(orch, RUN, head)


def test_a_run_without_events_ends_at_the_sha256_of_its_id(orch: Connection) -> None:
    assert verify(orch, RUN) == first_prev_hash(RUN)
    assert verify(orch, RUN, first_prev_hash(RUN)) == first_prev_hash(RUN)
    with pytest.raises(ChainError, match="recorded head"):
        verify(orch, RUN, b"\0" * 32)


def test_a_removed_last_event_is_found_against_the_recorded_head(
    orch: Connection, migrated_database: Database
) -> None:
    heads = [append(orch, RUN, event({"step": n}, n)) for n in (1, 2)]
    orch.commit()
    with migrated_database.server.connect(migrated_database.name) as conn:
        conn.execute("SET session_replication_role = replica")
        conn.execute("DELETE FROM run_events WHERE seq = 2")
    assert verify(orch, RUN) == heads[0]
    with pytest.raises(ChainError, match="recorded head"):
        verify(orch, RUN, heads[1])


ENVELOPES: dict[str, Callable[[dict[str, Any]], Any]] = {
    "extra key": lambda e: {**e, "extra": 1},
    "null type": lambda e: {**e, "type": None},
    "renamed key": lambda e: {("typ" if k == "type" else k): v for k, v in e.items()},
    "not an object": lambda e: [e],
    "not a time": lambda e: {**e, "time": "yesterday"},
    "a week date": lambda e: {**e, "time": "2026-W46-3"},
    "no time zone": lambda e: {**e, "time": e["time"].removesuffix("Z")},
    "a NUL character": lambda e: {**e, "data": {"k": "\x00"}},
    "seq a string": lambda e: {**e, "seq": "1"},
    "seq a boolean": lambda e: {**e, "seq": True},
    "seq a fraction": lambda e: {**e, "seq": 1.5},
    "data not an object": lambda e: {**e, "data": [1]},
    # Bytes as they are, not canonical JSON of a value:
    "not JSON": lambda e: b"not json",
    "nested too deeply": lambda e: b"[" * 100_000 + b"]" * 100_000,
}


@pytest.mark.parametrize("change", sorted(ENVELOPES))
def test_an_envelope_that_is_not_an_event_s_eight_fields_is_refused(
    orch: Connection, migrated_database: Database, change: str
) -> None:
    append(orch, RUN, event({"step": 1}))
    orch.commit()
    row = orch.execute(text("SELECT envelope, prev_hash FROM run_events")).one()
    value = ENVELOPES[change](json.loads(row.envelope))
    changed = value if isinstance(value, bytes) else canonical_json(value)
    with migrated_database.server.connect(migrated_database.name) as conn:
        conn.execute("SET session_replication_role = replica")
        conn.execute(
            "UPDATE run_events SET envelope = %s, hash = %s",
            (changed, event_hash(changed, row.prev_hash)),
        )
    with pytest.raises(ChainError, match="not an event's canonical JSON"):
        verify(orch, RUN)


def test_an_envelope_hashed_consistently_but_not_canonical_is_refused(
    orch: Connection, migrated_database: Database
) -> None:
    append(orch, RUN, event({"step": 1}))
    orch.commit()
    row = orch.execute(text("SELECT envelope, prev_hash FROM run_events")).one()
    spaced = row.envelope.replace(b'"seq":1', b'"seq": 1')
    with migrated_database.server.connect(migrated_database.name) as conn:
        conn.execute("SET session_replication_role = replica")
        conn.execute(
            "UPDATE run_events SET envelope = %s, hash = %s",
            (spaced, event_hash(spaced, row.prev_hash)),
        )
    with pytest.raises(ChainError, match="not an event's canonical JSON"):
        verify(orch, RUN)


@pytest.mark.parametrize(
    "data", [{"k": "a\x00b"}, {"a\x00": 1}, {"k": ["\x00"]}, {"k": {"n": "\x00"}}]
)
def test_an_event_with_a_nul_character_is_refused_before_anything_is_written(
    orch: Connection, data: dict[str, Any]
) -> None:
    with pytest.raises(EventError, match="NUL"):
        append(orch, RUN, event(data))
    assert append(orch, RUN, event({"step": 1}, 2))
    orch.commit()
    assert orch.execute(text("SELECT count(*) FROM run_events")).scalar_one() == 1


@pytest.mark.parametrize(
    "time",
    [
        datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=1))),
        datetime(9999, 12, 31, 23, tzinfo=timezone(timedelta(hours=-2))),
    ],
    ids=["before-year-1", "after-year-9999"],
)
def test_an_event_whose_time_has_no_utc_form_is_refused(orch: Connection, time: datetime) -> None:
    with pytest.raises(EventError, match="outside years 1 to 9999"):
        append(orch, RUN, event({}).model_copy(update={"time": time}))
    assert orch.execute(text("SELECT count(*) FROM run_events")).scalar_one() == 0


def test_an_event_s_subject_is_its_run(orch: Connection) -> None:
    with pytest.raises(EventError, match="subject"):
        append(orch, RUN, event({}).model_copy(update={"subject": RUN2}))
    append(orch, RUN, event({}).model_copy(update={"subject": RUN}))


@pytest.mark.parametrize("level", ["AUTOCOMMIT", "REPEATABLE READ", "SERIALIZABLE"])
def test_events_are_written_only_in_read_committed_transactions(
    orch: Connection, level: str
) -> None:
    orch.execution_options(isolation_level=level)
    with pytest.raises(ValueError, match="READ COMMITTED"):
        append(orch, RUN, event({}))
    orch.rollback()
    assert orch.execute(text("SELECT count(*) FROM run_events")).scalar_one() == 0


def test_concurrent_appends_form_one_chain(migrated_database: Database, orch: Connection) -> None:
    params = {k: str(v) for k, v in migrated_database.params("potka_orch").items()}
    start = threading.Barrier(4)
    errors: list[BaseException] = []

    def writer(number: int) -> None:
        try:
            with engine(params).connect() as conn:
                start.wait()
                for n in range(10):
                    append(conn, RUN, event({"writer": number, "n": n}, 100 * number + n))
                    conn.commit()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(w,)) for w in range(1, 5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert orch.execute(text("SELECT max(seq) FROM run_events")).scalar_one() == 40
    assert verify(orch, RUN) is not None


def test_sealing_takes_the_last_event_s_hash_as_the_chain_head(
    orch: Connection, migrated_database: Database
) -> None:
    with migrated_database.connect("potka_migrator") as conn:
        assign(conn)
    first = append(orch, RUN, event({"step": 1}, 1))
    orch.commit()
    with pytest.raises(SealingError, match="chain head"):
        seal(orch, RUN, TIME, 1, ARTIFACT, ARTIFACT, first_prev_hash(RUN))
    orch.rollback()
    seal(orch, RUN, TIME, 1, ARTIFACT, ARTIFACT, first)
    orch.commit()
    assert verify(orch, RUN, first) == first


@pytest.mark.parametrize("level", ["AUTOCOMMIT", "REPEATABLE READ"])
def test_sealing_also_needs_a_read_committed_transaction(
    orch: Connection, migrated_database: Database, level: str
) -> None:
    with migrated_database.connect("potka_migrator") as conn:
        assign(conn)
    orch.execution_options(isolation_level=level)
    with pytest.raises(ValueError, match="READ COMMITTED"):
        seal(orch, RUN, TIME, 1, ARTIFACT, ARTIFACT, first_prev_hash(RUN))


def test_sealing_waits_for_an_open_append_and_then_refuses_the_old_head(
    orch: Connection, migrated_database: Database
) -> None:
    with migrated_database.connect("potka_migrator") as conn:
        assign(conn)
    params = {k: str(v) for k, v in migrated_database.params("potka_orch").items()}
    append(orch, RUN, event({"step": 1}, 1))  # holds the run's lock, not committed
    outcome: list[BaseException | None] = []

    def sealer() -> None:
        try:
            with engine(params).connect() as conn:
                seal(conn, RUN, TIME, 1, ARTIFACT, ARTIFACT, first_prev_hash(RUN))
                conn.commit()
            outcome.append(None)
        except BaseException as exc:
            outcome.append(exc)

    thread = threading.Thread(target=sealer)
    thread.start()
    waiting = "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"
    with engine(params).connect() as watcher:
        for _ in range(200):
            if watcher.execute(text(waiting)).scalar_one() == 1:
                break
            watcher.rollback()
            time.sleep(0.05)
        else:
            pytest.fail("sealing did not wait for the open append's lock")
    orch.commit()
    thread.join(30)
    assert len(outcome) == 1
    assert isinstance(outcome[0], SealingError)
    assert "chain head" in str(outcome[0])


def chars(size: int) -> st.SearchStrategy[str]:
    """Strings of any character an event may hold (no NUL, no lone surrogate)."""
    return st.text(
        st.characters(exclude_characters="\x00", exclude_categories=["Cs"]), max_size=size
    )


json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**53), max_value=2**53)
    | st.floats(allow_nan=False, allow_infinity=False)
    | chars(8),
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(chars(4), inner, max_size=3),
    max_leaves=8,
)


@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(st.lists(st.dictionaries(chars(5), json_values, max_size=4), min_size=1, max_size=5))
def test_any_sequence_of_events_verifies(orch: Connection, payloads: list[dict[str, Any]]) -> None:
    run = new_run(orch)
    heads = [append(orch, run, event(data, next(COUNTER) + 1000)) for data in payloads]
    orch.commit()
    assert verify(orch, run) == heads[-1]


def new_run(orch: Connection) -> str:
    run = f"01HZY0000000000000000R{next(COUNTER):04d}"
    orch.execute(
        text(
            "INSERT INTO runs (id, campaign_id, repetition, seed_sampling, phase, official)"
            " VALUES (:id, :campaign, 0, 1, 'queued', false)"
        ),
        {"id": run, "campaign": CAMPAIGN},
    )
    return run


@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    st.lists(st.dictionaries(chars(3), json_values, max_size=3), min_size=1, max_size=4),
    st.integers(min_value=0),
    st.integers(min_value=0),
    st.integers(min_value=1, max_value=255),
)
def test_any_changed_byte_of_any_envelope_is_found(
    orch: Connection,
    migrated_database: Database,
    payloads: list[dict[str, Any]],
    which: int,
    where: int,
    xor: int,
) -> None:
    run = new_run(orch)
    head = [append(orch, run, event(data, next(COUNTER) + 1000)) for data in payloads][-1]
    orch.commit()
    seq = which % len(payloads) + 1
    body = orch.execute(
        text("SELECT envelope FROM run_events WHERE run_id = :run AND seq = :seq"),
        {"run": run, "seq": seq},
    ).scalar_one()
    changed = bytearray(body)
    changed[where % len(changed)] ^= xor
    with migrated_database.server.connect(migrated_database.name) as conn:
        conn.execute("SET session_replication_role = replica")
        conn.execute(
            "UPDATE run_events SET envelope = %s WHERE run_id = %s AND seq = %s",
            (bytes(changed), run, seq),
        )
    with pytest.raises(ChainError):
        verify(orch, run, head)
