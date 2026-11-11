"""CloudEvents 1.0 events of type potka.<service>.<event>."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from potka_core.events import Event, event_json, new_event
from potka_core.ids import new_id
from pydantic import ValidationError

VALID = {
    "id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    "source": "/potka/orch",
    "type": "potka.orch.training.started",
    "time": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    "data": {"run": 1},
}


def test_a_new_event() -> None:
    event = new_event("exec", "tool.executed", {"exit": 0}, subject=new_id())
    assert event.specversion == "1.0"
    assert event.source == "/potka/exec"
    assert event.type == "potka.exec.tool.executed"
    assert event.datacontenttype == "application/json"
    assert event.time.utcoffset() == timedelta(0)
    assert (event.data, len(event.subject or "")) == ({"exit": 0}, 26)


def test_the_json_form_keeps_null_data_floats_and_the_subject_and_puts_the_time_in_utc() -> None:
    plus_two = timezone(timedelta(hours=2))
    event = Event(
        **{**VALID, "time": datetime(2026, 1, 2, 5, 4, 5, 250000, tzinfo=plus_two)},  # type: ignore[arg-type]
        subject="01ARZ3NDEKTSV4RRFFQ69G5FAV",
    ).model_copy(update={"data": {"none": None, "x": 0.5}})
    fields = json.loads(event_json(event))
    assert fields["time"] == "2026-01-02T03:04:05.250000Z"
    assert fields["subject"] == "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    assert fields["data"] == {"none": None, "x": 0.5}


def test_the_json_form_is_canonical_with_a_utc_time() -> None:
    event = Event(**VALID)  # type: ignore[arg-type]
    assert event_json(event) == (
        b'{"data":{"run":1},"datacontenttype":"application/json",'
        b'"id":"01ARZ3NDEKTSV4RRFFQ69G5FAV","source":"/potka/orch","specversion":"1.0",'
        b'"time":"2026-01-02T03:04:05Z","type":"potka.orch.training.started"}'
    )
    assert Event.model_validate_json(event_json(event)) == event


@pytest.mark.parametrize(
    "change",
    [
        {"type": "pinka.orch.started"},
        {"type": "potka.orch"},
        {"type": "potka.Orch.started"},
        {"source": "/pinka/orch"},
        {"source": "orch"},
        {"id": "not-a-ulid"},
        {"specversion": "0.3"},
        {"time": datetime(2026, 1, 2, 3, 4, 5)},
        {"data": ["not", "an", "object"]},
        {"unknown": "field"},
        {"time": "2026-01-02T03:04:05Z"},  # a string, not a time: strict
        {"subject": ""},
        {"data": {"v": float("nan")}},
        {"data": {"v": 2**53 + 1}},
        {"data": {"v": chr(0xD800)}},
        {"data": {chr(0xD800): 1}},
    ],
)
def test_an_event_that_breaks_the_envelope_is_refused(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Event(**{**VALID, **change})  # type: ignore[arg-type]


def test_an_event_s_fields_cannot_be_reassigned() -> None:
    event = Event(**VALID)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        event.type = "potka.orch.other"
    assert json.loads(event_json(event))["type"] == "potka.orch.training.started"
