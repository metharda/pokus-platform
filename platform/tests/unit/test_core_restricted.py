"""A restricted value never shows itself."""

from __future__ import annotations

import copy
import json
import pickle

import pytest
from potka_core.canonical import CanonicalJSONError, canonical_json
from potka_core.events import new_event
from potka_core.restricted import MARKER, Restricted
from pydantic import BaseModel, JsonValue, ValidationError

TRUTH = "scenario-FIXTURE-disk-full"


def test_it_never_shows_its_value() -> None:
    value = Restricted(TRUTH)
    shown = [
        str(value),
        repr(value),
        f"{value}",
        f"{value!r}",
        f"{value!a}",
        f"{value:>40}",
        "%s" % value,  # noqa: UP031
        "%r" % value,  # noqa: UP031
        "{} {!r}".format(value, value),  # noqa: UP032
    ]
    assert shown == [MARKER] * 8 + [f"{MARKER} {MARKER}"]  # a format spec is ignored
    assert str(value) == MARKER
    assert value.reveal() == TRUTH


def test_it_is_refused_by_json_pickle_and_canonical_json() -> None:
    value = Restricted(TRUTH)
    with pytest.raises(TypeError):
        json.dumps({"truth": value})
    for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
        with pytest.raises(TypeError):
            pickle.dumps(value, protocol)
    with pytest.raises(TypeError):
        value.__getstate__()
    with pytest.raises(CanonicalJSONError):
        canonical_json({"truth": value})  # type: ignore[dict-item]


def test_it_cannot_enter_an_event_or_a_json_field() -> None:
    class Data(BaseModel):
        value: JsonValue

    with pytest.raises(ValidationError) as raised:
        Data(value=Restricted(TRUTH))  # type: ignore[arg-type]
    assert TRUTH not in str(raised.value)
    with pytest.raises(ValidationError) as refused:
        new_event("inject", "fault.injected", {"fault": Restricted(TRUTH)})  # type: ignore[dict-item]
    assert TRUTH not in str(refused.value)


def test_copies_stay_restricted_and_it_has_no_attributes_to_read() -> None:
    value = Restricted(TRUTH)
    for copied in (copy.copy(value), copy.deepcopy(value)):
        assert isinstance(copied, Restricted)
        assert repr(copied) == MARKER
        assert copied.reveal() == TRUTH
    assert not hasattr(value, "__dict__")
    with pytest.raises(TypeError):
        vars(value)


def test_equality_is_by_value_and_the_hash_is_not_the_value_s() -> None:
    assert Restricted(TRUTH) == Restricted(TRUTH)
    assert Restricted(TRUTH) != TRUTH
    assert Restricted(TRUTH) != Restricted("other")
    assert {Restricted(TRUTH): 1}[Restricted(TRUTH)] == 1
    assert hash(Restricted(42)) != hash(42)
