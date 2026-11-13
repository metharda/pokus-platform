"""A scenario's parameters: the derivation from a seed, and the templates."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from potka_core.canonical import canonical_json
from potka_core.catalog.parameters import (
    SEED_MAX,
    SEED_MIN,
    Choice,
    Parameter,
    Range,
    Scalar,
    TemplateError,
    derive,
    derive_all,
    placeholders,
    render,
    render_text,
    tree_placeholders,
)
from pydantic import JsonValue, TypeAdapter, ValidationError

PARAMETER: TypeAdapter[Choice | Range] = TypeAdapter(Parameter)
SEEDS = st.integers(min_value=SEED_MIN, max_value=SEED_MAX)
NAMES = st.from_regex(r"[a-z][a-z0-9_]{0,20}", fullmatch=True)
SCALARS = st.one_of(
    st.text(max_size=8),
    st.integers(min_value=-(2**53) + 1, max_value=2**53 - 1),
    st.booleans(),
    st.floats(allow_nan=False, allow_infinity=False),
)


def parameter(data: dict[str, Any]) -> Choice | Range:
    return PARAMETER.validate_json(json.dumps(data))


def choice(*values: object) -> Choice:
    made = parameter({"type": "choice", "values": list(values)})
    assert isinstance(made, Choice)
    return made


def interval(low: int, high: int, step: int = 1) -> Range:
    made = parameter({"type": "range", "min": low, "max": high, "step": step})
    assert isinstance(made, Range)
    return made


# HMAC-SHA256 of the name, keyed with the seed's eight bytes, as openssl computes it
# (openssl dgst -sha256 -mac HMAC -macopt hexkey:<the seed's bytes>).
DIGESTS = [
    (1, "port", "eb6eef775e492eefacfe6eebdf3cc2756a5b2b732c768fe13e47ac7aade08693"),
    (-1, "port", "317044982025165716c6074fba1bfaa62c533d7c23b375e04f907d50917919f2"),
    (0, "site", "6b626ece20c1dd77b1257193346c2b772ad72b44afb55f21bd05ab65dd566b51"),
    (SEED_MAX, "fill_percent", "2b24396b7c4ce13230736e60f5089704a18135265bab9a2028d533025855573a"),
    (SEED_MIN, "fill_percent", "e4d4b0789239e1a278eeb4f60a112ed095a9e7ae0fe1d45e46c0ef0819610189"),
]


@pytest.mark.parametrize(("seed", "name", "digest"), DIGESTS)
def test_a_value_is_the_candidate_the_digest_counts_to(seed: int, name: str, digest: str) -> None:
    for count in (1, 2, 3, 7, 10):
        values = [f"v{i}" for i in range(count)]
        assert derive(seed, name, choice(*values)) == values[int(digest, 16) % count]
        assert derive(seed, name, interval(5, 5 + 3 * (count - 1), 3)) == 5 + 3 * (
            int(digest, 16) % count
        )


def test_known_values() -> None:
    # The digests' remainders, computed with bc: 1 mod 3, 2 mod 4, 2 mod 3, 3 mod 5, 4 mod 5.
    assert derive(1, "port", choice(8081, 8082, 8083)) == 8082
    assert derive(-1, "port", choice("a", "b", "c", "d")) == "c"
    assert derive(0, "site", choice("blog", "shop", "docs")) == "docs"
    assert derive(SEED_MAX, "fill_percent", interval(90, 98, 2)) == 96
    assert derive(SEED_MIN, "fill_percent", interval(90, 98, 2)) == 98
    both: dict[str, Choice | Range] = {
        "port": choice(8081, 8082, 8083),
        "fill_percent": interval(90, 98, 2),
    }
    # The digest of "fill_percent" with the seed 1 (ee822520…8a1b) is 4 modulo 5.
    assert derive_all(1, both) == {"port": 8082, "fill_percent": 98}
    assert list(derive_all(1, both)) == ["port", "fill_percent"]


def test_a_value_keeps_its_type() -> None:
    values = (True, 1, 1.5, "1")
    seen = {type(derive(seed, "x", choice(*values))) for seed in range(64)}
    assert seen == {bool, int, float, str}


@given(
    seed=SEEDS,
    name=NAMES,
    values=st.lists(SCALARS, min_size=1, max_size=6, unique_by=canonical_json),
)
def test_a_choice_gives_one_of_its_values_and_always_the_same(
    seed: int, name: str, values: list[object]
) -> None:
    picked = derive(seed, name, choice(*values))
    assert any(picked is v or (type(picked) is type(v) and picked == v) for v in values)
    assert derive(seed, name, choice(*values)) == picked


@given(
    seed=SEEDS,
    name=NAMES,
    low=st.integers(min_value=-(10**6), max_value=10**6),
    steps=st.integers(min_value=0, max_value=10**6),
    step=st.integers(min_value=1, max_value=1000),
)
def test_a_range_gives_a_whole_number_of_steps_from_min(
    seed: int, name: str, low: int, steps: int, step: int
) -> None:
    high = low + steps * step
    value = derive(seed, name, interval(low, high, step))
    assert type(value) is int
    assert low <= value <= high
    assert (value - low) % step == 0


def test_the_candidates_are_about_equally_likely() -> None:
    values = ("a", "b", "c", "d")
    counts = Counter(derive(seed, "x", choice(*values)) for seed in range(-10_000, 10_000))
    assert set(counts) == set(values)
    assert all(4_700 <= count <= 5_300 for count in counts.values()), counts
    counts = Counter(derive(seed, "x", interval(0, 9)) for seed in range(20_000))
    assert sorted(counts) == list(range(10))
    assert all(1_800 <= count <= 2_200 for count in counts.values()), counts


def test_two_parameters_of_one_seed_are_independent() -> None:
    pairs = Counter(
        (derive(seed, "first", interval(0, 1)), derive(seed, "second", interval(0, 1)))
        for seed in range(4_000)
    )
    assert len(pairs) == 4
    assert all(850 <= count <= 1_150 for count in pairs.values()), pairs


@pytest.mark.parametrize("seed", [SEED_MIN - 1, SEED_MAX + 1, True, 1.0, "1", None])
def test_a_seed_that_is_not_a_signed_64_bit_integer_is_refused(seed: Any) -> None:
    with pytest.raises(ValueError, match=r"^the scenario seed is not a signed 64-bit integer$"):
        derive(seed, "x", choice("a"))


@pytest.mark.parametrize(
    "data",
    [
        {"type": "choice", "values": []},
        {"type": "choice", "values": ["a", "a"]},
        {"type": "choice", "values": [1, 1]},
        {"type": "choice", "values": [["a"]]},
        {"type": "choice", "values": [{"a": 1}]},
        {"type": "choice", "values": [None]},
        {"type": "choice", "values": [2**53]},
        {"type": "choice", "values": [-(2**53)]},
        {"type": "choice", "values": "ab"},
        {"type": "choice"},
        {"type": "choice", "values": ["a"], "weights": [1]},
        {"type": "range", "min": 2, "max": 1},
        {"type": "range", "min": 1, "max": 4, "step": 2},
        {"type": "range", "min": 1, "max": 3, "step": 0},
        {"type": "range", "min": 1, "max": 3, "step": -1},
        {"type": "range", "min": 1.0, "max": 3},
        {"type": "range", "min": "1", "max": 3},
        {"type": "range", "min": True, "max": 3},
        {"type": "range", "min": 1},
        {"type": "range", "min": 0, "max": 2**53},
        {"type": "range", "min": -(2**53), "max": 0},
        {"type": "range", "min": 0, "max": 0, "step": 2**53},
        {"type": "range", "min": 1, "max": 3, "values": [1]},
        {"type": "pick", "values": ["a"]},
        {"values": ["a"]},
    ],
)
def test_a_malformed_parameter_is_refused(data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        parameter(data)


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_a_choice_of_a_number_json_cannot_hold_is_refused(number: str) -> None:
    with pytest.raises(ValidationError):
        PARAMETER.validate_json(f'{{"type": "choice", "values": [{number}]}}')


def test_a_choices_values_differ_as_canonical_json_writes_them() -> None:
    values = (True, 1, "1", False, 0, 1.5, "true", "")
    kept = choice(*values).values
    assert kept == values
    assert [type(value) for value in kept] == [type(value) for value in values]


@pytest.mark.parametrize(
    "values", [[1, 1.0], [0.0, -0.0], [0, -0.0], [100, 1e2], [1.5, 1.50], ["a", "b", "a"]]
)
def test_two_numbers_of_one_value_are_one_value(values: list[object]) -> None:
    with pytest.raises(ValidationError, match="a choice lists a value twice"):
        choice(*values)


def test_a_range_of_one_value_and_its_largest_bounds() -> None:
    assert list(interval(7, 7).candidates()) == [7]
    assert list(interval(1, 9, 4).candidates()) == [1, 5, 9]
    wide = interval(-(2**53) + 1, 2**53 - 1)
    assert len(wide.candidates()) == 2**54 - 1
    assert derive(0, "x", wide) in wide.candidates()


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("", ()),
        ("no placeholder", ()),
        ("${a}", ("a",)),
        ("port ${port} of ${site}, again ${port}", ("port", "site", "port")),
        ("costs 5 $$", ()),
        ("$${a}", ()),
        ("$$${a}", ("a",)),
        ("${a}${b_2}", ("a", "b_2")),
        ("{a} $${b} }${c}{", ("c",)),
    ],
)
def test_the_placeholders_of_a_text(text: str, names: tuple[str, ...]) -> None:
    assert placeholders(text) == names


@pytest.mark.parametrize(
    ("text", "place"),
    [
        ("$", 1),
        ("a $ b", 3),
        ("$a", 1),
        ("${A}", 1),
        ("${}", 1),
        ("${a", 1),
        ("${a b}", 1),
        ("${1a}", 1),
        ("${a-b}", 1),
        ("$$$", 3),
        ("ok ${a} then $(b)", 14),
        ("${" + "a" * 64 + "}", 1),
    ],
)
def test_a_stray_dollar_sign_is_refused_with_its_place_only(text: str, place: int) -> None:
    message = f"the '$' at character {place} is neither '$$' nor the start of '${{name}}'"
    with pytest.raises(TemplateError) as caught:
        placeholders(text)
    assert str(caught.value) == message
    with pytest.raises(TemplateError) as caught:
        render_text(text, {"a": "x"})
    assert str(caught.value) == message


def test_a_text_is_rendered_with_its_values_as_json_writes_them() -> None:
    values: dict[str, Scalar] = {
        "site": "blog",
        "port": 8082,
        "ratio": 0.5,
        "on": True,
        "off": False,
    }
    assert render_text("${site}:${port} ${ratio} ${on}/${off} for 5 $$", values) == (
        "blog:8082 0.5 true/false for 5 $"
    )
    assert render_text("$${site}", values) == "${site}"
    assert render_text("${site}", values) == "blog"
    assert render_text("${port}", values) == "8082"
    assert render_text("no placeholder", {}) == "no placeholder"


def test_a_value_that_looks_like_a_template_is_not_rendered_again() -> None:
    assert render_text("${a}", {"a": "${b}", "b": "x"}) == "${b}"
    assert render("${a}", {"a": "$"}) == "$"


def test_a_string_that_is_one_placeholder_becomes_the_value_itself() -> None:
    values: dict[str, Scalar] = {"site": "blog", "port": 8082, "ratio": 0.5, "on": True}
    tree: dict[str, JsonValue] = {
        "port": "${port}",
        "url": "http://${site}:${port}/",
        "flags": ["${on}", "${ratio}", " ${port}", "${port} ", "$${port}"],
        "nested": {"${site}": {"deep": ["${site}"]}},
        "number": 5,
        "nothing": None,
    }
    assert render(tree, values) == {
        "port": 8082,
        "url": "http://blog:8082/",
        "flags": [True, 0.5, " 8082", "8082 ", "${port}"],
        "nested": {"${site}": {"deep": ["blog"]}},
        "number": 5,
        "nothing": None,
    }
    assert type(render("${on}", values)) is bool
    assert tree["port"] == "${port}"
    assert tree_placeholders(tree) == (
        "port",
        "site",
        "port",
        "on",
        "ratio",
        "port",
        "port",
        "site",
    )


@pytest.mark.parametrize("text", ["${missing}", "a ${missing} b", "${a}${missing}"])
def test_a_placeholder_without_a_value_is_refused(text: str) -> None:
    with pytest.raises(TemplateError, match=r"^no parameter missing$"):
        render_text(text, {"a": "x"})
    with pytest.raises(TemplateError, match=r"^no parameter missing$"):
        render({"k": [text]}, {"a": "x"})


@given(text=st.text())
def test_a_text_with_its_dollar_signs_doubled_renders_as_itself(text: str) -> None:
    escaped = text.replace("$", "$$")
    assert placeholders(escaped) == ()
    assert render_text(escaped, {}) == text
    assert render(escaped, {}) == text


@given(
    parts=st.lists(st.text(alphabet=st.characters(exclude_characters="$"), max_size=5), max_size=5),
    values=st.dictionaries(NAMES, SCALARS, min_size=1, max_size=3),
    data=st.data(),
)
def test_a_template_renders_as_its_parts_joined(
    parts: list[str], values: dict[str, Any], data: st.DataObject
) -> None:
    names = [data.draw(st.sampled_from(sorted(values))) for _ in parts]
    template = "".join(part + "${" + name + "}" for part, name in zip(parts, names, strict=True))
    expected = "".join(
        part + (values[name] if isinstance(values[name], str) else json.dumps(values[name]))
        for part, name in zip(parts, names, strict=True)
    )
    assert placeholders(template) == tuple(names)
    assert render_text(template, values) == expected
