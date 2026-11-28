"""The decision's Rego, read as text: what its own cases cannot say of it.

The cases (``policy/potka/action_test.rego``) run under OPA (``make
policy``). These tests need no OPA: they hold the list of rules, that
every rule has a case of its name with the answer the rule gives, and
that no denial is written in the one form that fails open.
"""

from __future__ import annotations

import re
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[2]
REGO = (PLATFORM / "policy" / "potka" / "action.rego").read_text(encoding="utf-8")
CASES = (PLATFORM / "policy" / "potka" / "action_test.rego").read_text(encoding="utf-8")
EFFECTS = {"allow", "deny", "require_approval"}
# The rules in the order they are tried.
RULES = [
    ("input_not_understood", "deny"),
    ("tool_unknown", "deny"),
    ("tool_forbidden", "deny"),
    ("orchestrator_never_mutates", "deny"),
    ("tool_not_offered", "deny"),
    ("call_invalid", "deny"),
    ("caller_unknown", "deny"),
    ("evidence_profile", "allow"),
    ("read_only", "allow"),
    ("diagnose_only", "deny"),
    ("recommend", "deny"),
    ("mode_unknown", "deny"),
    ("diagnosis_required", "deny"),
    ("diagnosis_inconsistent", "deny"),
    ("repeat_budget", "deny"),
    ("mutating_budget", "deny"),
    ("high_risk", "require_approval"),
    ("medium_risk", "require_approval"),
    ("low_risk_confident", "allow"),
    ("low_risk_unsure", "require_approval"),
    ("low_risk_unsure_autonomous", "deny"),
]
# What holds when no rule did: an input the rules do not know is not understood either.
DEFAULT_RULE = ("input_not_understood", "deny")
ANSWER = re.compile(
    r'answer\("(?P<effect>[a-z_]+)", "(?P<rule>[a-z_]+)", (?P<reason>"[a-z_]+"|[a-z_]+)\)'
)
DEFAULT = re.compile(
    r'^default verdict := \{"effect": "(?P<effect>[a-z_]+)", "rule_id": "(?P<rule>[a-z_]+)",'
    r' "reason": "(?P<reason>[a-z_]+)"\}$',
    re.MULTILINE,
)
EXPECTED = re.compile(
    r'whole\("(?P<effect>[a-z_]+)", "(?P<rule>[a-z_]+)", "(?P<reason>[a-z_:]+)"\)'
)
CASE = re.compile(r"^(?P<name>test_[a-z0-9_]+) if \{\n(?P<body>.*?)^\}$", re.MULTILINE | re.DOTALL)


def code(text: str) -> list[str]:
    """TEXT's lines without its comments."""
    return [line.split("#", 1)[0].rstrip() for line in text.splitlines()]


def answers() -> list[tuple[str, str, str]]:
    """Every answer of the decision, in the order of the text: rule, effect, reason."""
    found = [(m["rule"], m["effect"], m["reason"].strip('"')) for m in ANSWER.finditer(REGO)]
    default = DEFAULT.search(REGO)
    assert default is not None
    return [*found, (default["rule"], default["effect"], default["reason"])]


def cases() -> dict[str, str]:
    """Every case of the cases' file, by its name, with its body."""
    return {m["name"]: m["body"] for m in CASE.finditer(CASES)}


def test_the_rules_are_these_in_this_order_each_with_its_effect() -> None:
    assert [(rule, effect) for rule, effect, _ in answers()] == [*RULES, DEFAULT_RULE]
    assert {effect for _, effect in RULES} == EFFECTS
    assert len({rule for rule, _ in RULES}) == len(RULES)
    assert RULES[0] == DEFAULT_RULE


def test_the_last_answer_of_the_chain_is_before_the_default_in_no_other_place() -> None:
    # The chain is one rule: every answer but the default is an `else` of the first.
    chain = [line for line in code(REGO) if "answer(" in line and "answer(effect" not in line]
    assert len(chain) == len(RULES)
    assert chain[0].startswith("verdict := answer(")
    assert all(line.startswith("} else := answer(") for line in chain[1:])


def test_every_branch_of_the_decision_answers_through_the_one_helper() -> None:
    # A branch written as an object of its own, or a second rule that gives a verdict,
    # would be a rule that the list above does not see.
    lines = code(REGO)
    branches = [line for line in lines if re.match(r"(verdict\b|\} else\b)", line)]
    assert len(branches) == len(RULES)
    assert all(ANSWER.search(line) and line.endswith(" if {") for line in branches)
    assert len([line for line in lines if re.match(r"default verdict\b", line)]) == 1
    assert len([line for line in lines if re.match(r"decision\b", line)]) == 1
    assert [line for line in lines if " else " in line and line not in branches] == []
    # An effect is written in two places: the default's object and the helper's.
    assert "\n".join(lines).count('"effect"') == 2


def test_every_rule_has_a_case_of_its_name_that_expects_its_answer() -> None:
    found = cases()
    named = {name.removeprefix("test_rule_") for name in found if name.startswith("test_rule_")}
    assert named == {rule for rule, _ in RULES}
    for rule, effect, reason in answers():
        # The first rule and the default share a name: the case of that name expects both.
        expected = EXPECTED.search(found[f"test_rule_{rule}"])
        assert expected is not None, rule
        assert (expected["rule"], expected["effect"]) == (rule, effect)
        if rule == "call_invalid":
            # Its reason ends in the call's own code.
            assert reason == "invalid_reason"
            assert expected["reason"].startswith("invalid_call:")
        else:
            assert expected["reason"] == reason


def test_a_case_expects_no_rule_the_decision_has_not() -> None:
    known = {rule for rule, _ in RULES}
    expected = {m["rule"] for m in EXPECTED.finditer(CASES)}
    # A rule compared by its ID alone.
    compared = set(
        re.findall(r'^[^\n]*(?:\brule\(|rule_id)[^\n]*== "([a-z_]+)"$', CASES, re.MULTILINE)
    )
    assert expected and compared
    assert expected | compared <= known


def test_every_denial_negates_a_rule_by_its_name_and_nothing_else() -> None:
    # `not` before an expression that reads the input is undefined, not true, when what
    # it reads is missing: the denial would not hold, and a later rule could allow.
    negated = [line.strip() for line in code(REGO) if re.search(r"\bnot\b", line)]
    assert negated
    assert [line for line in negated if not re.fullmatch(r"not [a-z_]+", line)] == []
    defined = set(re.findall(r"^([a-z_]+) if", REGO, re.MULTILINE))
    assert {line.removeprefix("not ") for line in negated} <= defined


def test_the_decision_reads_a_tools_facts_from_the_bundles_data_alone() -> None:
    lines = code(REGO)
    assert "tool := data.tools[input.tool.name]" in lines
    for fact in ("risk", "mutating", "applicable_categories"):
        assert not [line for line in lines if f"input.tool.{fact}" in line]
        assert [line for line in lines if f"tool.{fact}" in line]


def test_every_case_is_named_once() -> None:
    names = re.findall(r"^(test_[a-z0-9_]+) if", CASES, re.MULTILINE)
    assert len(names) == len(set(names))
    assert set(names) == set(cases())
