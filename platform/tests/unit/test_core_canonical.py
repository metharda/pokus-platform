"""Canonical JSON against RFC 8785's own test vectors, and its properties."""

from __future__ import annotations

import json
import random
import struct

import pytest
from hypothesis import given
from hypothesis import strategies as st
from potka_core.canonical import CanonicalJSONError, JSONValue, canonical_json

# RFC 8785's vectors, copied from its text (rfc-editor.org/rfc/rfc8785.txt).
# Section 3.2.2: the sample input.
SAMPLE = r"""{
  "numbers": [333333333.33333329, 1E30, 4.50,
              2e-3, 0.000000000000000000000000001],
  "string": "\u20ac$\u000F\u000aA'\u0042\u0022\u005c\\\"\/",
  "literals": [null, true, false]
}"""
# Section 3.2.4: its canonical UTF-8 bytes.
SAMPLE_BYTES = bytes.fromhex(
    "7b 22 6c 69 74 65 72 61 6c 73 22 3a 5b 6e 75 6c 6c 2c 74 72"
    " 75 65 2c 66 61 6c 73 65 5d 2c 22 6e 75 6d 62 65 72 73 22 3a"
    " 5b 33 33 33 33 33 33 33 33 33 2e 33 33 33 33 33 33 33 2c 31"
    " 65 2b 33 30 2c 34 2e 35 2c 30 2e 30 30 32 2c 31 65 2d 32 37"
    " 5d 2c 22 73 74 72 69 6e 67 22 3a 22 e2 82 ac 24 5c 75 30 30"
    " 30 66 5c 6e 41 27 42 5c 22 5c 5c 5c 5c 5c 22 2f 22 7d"
)
# Section 3.2.3: the sorting test data and the expected order of its values.
SORTING = r"""{
  "\u20ac": "Euro Sign",
  "\r": "Carriage Return",
  "\ufb33": "Hebrew Letter Dalet With Dagesh",
  "1": "One",
  "\ud83d\ude00": "Emoji: Grinning Face",
  "\u0080": "Control",
  "\u00f6": "Latin Small Letter O With Diaeresis"
}"""
SORTED_VALUES = [
    "Carriage Return",
    "One",
    "Control",
    "Latin Small Letter O With Diaeresis",
    "Euro Sign",
    "Emoji: Grinning Face",
    "Hebrew Letter Dalet With Dagesh",
]
# Appendix B: IEEE 754 values (hexadecimal) and their JSON representation.
NUMBERS = [
    ("0000000000000000", "0"),
    ("8000000000000000", "0"),
    ("0000000000000001", "5e-324"),
    ("8000000000000001", "-5e-324"),
    ("7fefffffffffffff", "1.7976931348623157e+308"),
    ("ffefffffffffffff", "-1.7976931348623157e+308"),
    ("4340000000000000", "9007199254740992"),
    ("c340000000000000", "-9007199254740992"),
    ("4430000000000000", "295147905179352830000"),
    ("44b52d02c7e14af5", "9.999999999999997e+22"),
    ("44b52d02c7e14af6", "1e+23"),
    ("44b52d02c7e14af7", "1.0000000000000001e+23"),
    ("444b1ae4d6e2ef4e", "999999999999999700000"),
    ("444b1ae4d6e2ef4f", "999999999999999900000"),
    ("444b1ae4d6e2ef50", "1e+21"),
    ("3eb0c6f7a0b5ed8c", "9.999999999999997e-7"),
    ("3eb0c6f7a0b5ed8d", "0.000001"),
    ("41b3de4355555553", "333333333.3333332"),
    ("41b3de4355555554", "333333333.33333325"),
    ("41b3de4355555555", "333333333.3333333"),
    ("41b3de4355555556", "333333333.3333334"),
    ("41b3de4355555557", "333333333.33333343"),
    ("becbf647612f3696", "-0.0000033333333333333333"),
    ("43143ff3c1cb0959", "1424953923781206.2"),
]
NOT_NUMBERS = ["7fffffffffffffff", "7ff0000000000000"]  # NaN, Infinity


def ieee754(hexadecimal: str) -> float:
    value: float = struct.unpack(">d", bytes.fromhex(hexadecimal))[0]
    return value


def test_the_rfc_sample_gives_its_bytes() -> None:
    assert canonical_json(json.loads(SAMPLE)) == SAMPLE_BYTES


def test_the_rfc_sorting_test() -> None:
    canonical = canonical_json(json.loads(SORTING))
    assert list(json.loads(canonical).values()) == SORTED_VALUES


@pytest.mark.parametrize(("hexadecimal", "text"), NUMBERS)
def test_the_rfc_number_samples(hexadecimal: str, text: str) -> None:
    assert canonical_json([ieee754(hexadecimal)]) == f"[{text}]".encode()


@pytest.mark.parametrize(
    "value",
    [
        [ieee754(NOT_NUMBERS[0])],
        [ieee754(NOT_NUMBERS[1])],
        [-ieee754(NOT_NUMBERS[1])],
        {"a": 2**53 + 1},
        {"a": -(2**53) - 1},
        {"a": 2**68},
        {"a": 10**400},
        {"a": "\ud800"},
        {1: "x"},
        {"a": b"x"},
        {"a": {1, 2}},
    ],
    ids=[
        "nan",
        "infinity",
        "-infinity",
        "2**53+1",
        "-2**53-1",
        "2**68 exactly",
        "10**400",
        "surrogate",
        "key",
        "bytes",
        "set",
    ],
)
def test_values_without_a_canonical_form_are_refused(value: object) -> None:
    with pytest.raises(CanonicalJSONError):
        canonical_json(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [{chr(0xD800): 1}, [10**5000], {"a": 2**53 + 12345}],
    ids=["surrogate-key", "5000-digits", "2**53+12345"],
)
def test_refusals_never_show_the_value(value: JSONValue) -> None:
    with pytest.raises(CanonicalJSONError) as raised:
        canonical_json(value)
    assert "12345" not in str(raised.value)
    assert "9007" not in str(raised.value)


@given(st.integers())
def test_any_integer_is_refused_or_reads_back_to_the_same_bytes(value: int) -> None:
    try:
        canonical = canonical_json([value])
    except CanonicalJSONError:
        assert abs(value) > 2**53 - 1
        return
    assert canonical_json(json.loads(canonical)) == canonical


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (2**53, "9007199254740992"),
        (-(2**53), "-9007199254740992"),
        (295147905179352830000, "295147905179352830000"),
        (39975768994693990, "39975768994693990"),
    ],
)
def test_an_integer_in_a_double_s_canonical_text_is_that_double(value: int, text: str) -> None:
    assert canonical_json([value]) == f"[{text}]".encode()
    assert canonical_json([float(value)]) == f"[{text}]".encode()


JSON = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(-(2**53) + 1, 2**53 - 1)
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(),
    lambda children: st.lists(children) | st.dictionaries(st.text(), children),
    max_leaves=25,
)


@given(st.dictionaries(st.text(), JSON), st.randoms(use_true_random=False))
def test_the_bytes_do_not_depend_on_key_order(
    value: dict[str, JSONValue], rnd: random.Random
) -> None:
    keys = list(value)
    rnd.shuffle(keys)
    assert canonical_json({k: value[k] for k in keys}) == canonical_json(value)


@given(JSON)
def test_canonical_json_read_back_gives_the_same_bytes(value: JSONValue) -> None:
    canonical = canonical_json(value)
    assert canonical_json(json.loads(canonical)) == canonical


@given(
    st.recursive(
        st.none() | st.booleans() | st.integers(-(2**53) + 1, 2**53 - 1) | st.text(),
        lambda children: st.lists(children) | st.dictionaries(st.text(), children),
        max_leaves=25,
    )
)
def test_canonical_json_without_floats_reads_back_as_the_value(value: JSONValue) -> None:
    assert json.loads(canonical_json(value)) == value
