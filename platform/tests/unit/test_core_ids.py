"""ULIDs in their canonical form."""

from __future__ import annotations

import time

import pytest
from potka_core.ids import Id, check_id, new_id
from pydantic import BaseModel, ValidationError


class Record(BaseModel):
    id: Id


def test_a_new_id_is_a_canonical_ulid() -> None:
    value = new_id()
    assert len(value) == 26
    assert check_id(value) == value
    assert Record(id=value).id == value


def test_ids_sort_in_time_order() -> None:
    first = new_id()
    time.sleep(0.002)
    assert new_id() > first


@pytest.mark.parametrize(
    "value",
    [
        "01arz3ndektsv4rrffq69g5fav",  # not uppercase
        "01ARZ3NDEKTSV4RRFFQ69G5FAU",  # U is not in the alphabet
        "01ARZ3NDEKTSV4RRFFQ69G5FA",  # 25 characters
        "81ARZ3NDEKTSV4RRFFQ69G5FAV",  # more than 128 bits
        "",
    ],
)
def test_other_strings_are_refused(value: str) -> None:
    with pytest.raises(ValueError):
        check_id(value)
    with pytest.raises(ValidationError):
        Record(id=value)
