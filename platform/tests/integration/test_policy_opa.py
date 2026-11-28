"""The release's bundle, asked through the executor's client, by the lab's OPA.

The server is the binary of ``make opa`` with the release's bundle and an
authorization rule that allows the decision's path and health alone, as
the lab's documentation describes its own (``potka_testing.opa``). The
questions are made with the executor's own builder from calls the tool
registry's checks passed or refused.
"""

from __future__ import annotations

import shutil
import socket
from pathlib import Path
from typing import Any

import httpx
import pytest
from potka_executor.bundle import HASH_FILE, bundle_hash, hash_file, kept_hash
from potka_executor.opa import AnotherBundle, Opa, PolicyUnavailable
from potka_executor.policy import (
    NO_DIAGNOSIS,
    NOT_OFFERED,
    UNKNOWN_TOOL,
    Budgets,
    Counters,
    Diagnosis,
    Judged,
    Thresholds,
    policy_input,
)
from potka_testing import opa as testing_opa
from potka_testing.opa import BUNDLE, DECISION_PATH, server
from potka_testing.tool_context import context
from potka_tools.arguments import ArgumentError, check, check_meta
from potka_tools.registry import load_registry

PLATFORM = Path(__file__).resolve().parents[2]
REGISTRY = load_registry(PLATFORM / "tools" / "registry")
CONTEXT = context()
HASH = bundle_hash(BUNDLE)
META = {"action_confidence": 0.9, "rationale": "It is stopped.", "expected_effect": "It runs."}
DIFF = "--- a/default\n+++ b/default\n@@ -1 +1 @@\n-listen 81;\n+listen 80;\n"
PATCH = {"node": "web", "path": "/etc/nginx/sites-available/default", "diff": DIFF}
UNIT = {"node": "web", "unit": "nginx.service"}


def valid(name: str, args: dict[str, Any], meta: dict[str, Any] | None = None) -> Judged:
    tool = REGISTRY.tool(name)
    assert tool is not None
    call = check(tool, args, CONTEXT)
    return Judged(name=name, known=True, offered=True, call=call, meta=check_meta(tool, meta))


def refused(name: str, args: dict[str, Any]) -> Judged:
    tool = REGISTRY.tool(name)
    assert tool is not None
    with pytest.raises(ArgumentError) as error:
        check(tool, args, CONTEXT)
    return Judged(name=name, known=True, offered=True, error=error.value.code)


def asked(judged: Judged, **other: Any) -> dict[str, Any]:
    given: dict[str, Any] = {
        "caller": "agent",
        "mode": "autonomous_low_risk",
        "budgets": Budgets(max_mutating_calls=5, max_same_action_repeats=2),
        "thresholds": Thresholds(min_action_confidence=0.6, min_diagnosis_confidence=0.5),
        "diagnosis": Diagnosis(present=True, label="service.stopped", confidence=0.9),
        "counters": Counters(steps=3, mutating_calls=0, same_action_repeats=0),
    }
    return policy_input(**{**given, **other}, judged=judged)


@pytest.fixture(scope="module")
def engine(opa_server: str) -> Any:
    client = Opa(opa_server, DECISION_PATH, HASH)
    yield client
    client.close()


def test_the_releases_bundle_holds_the_hash_of_its_own_files() -> None:
    assert kept_hash(BUNDLE) == HASH


def test_the_engine_serves_the_releases_bundle_and_denies_an_empty_question(engine: Opa) -> None:
    engine.check()
    decision = engine.decide({})
    assert (decision.effect, decision.rule_id, decision.bundle_hash) == (
        "deny",
        "input_not_understood",
        HASH,
    )


QUESTIONS = [
    (
        "a-low-risk-action",
        lambda: asked(valid("start_service", UNIT, META)),
        "allow",
        "low_risk_confident",
        "low_risk_confident",
    ),
    (
        "a-medium-risk-action",
        lambda: asked(valid("restart_service", UNIT, META)),
        "require_approval",
        "medium_risk",
        "medium_risk",
    ),
    (
        "a-patch",
        lambda: asked(
            valid("apply_config_patch", PATCH, META),
            diagnosis=Diagnosis(present=True, label="config.syntax_error", confidence=0.9),
        ),
        "require_approval",
        "medium_risk",
        "medium_risk",
    ),
    (
        "a-read",
        lambda: asked(valid("read_file", {"node": "web", "path": "/etc/nginx/nginx.conf"})),
        "allow",
        "read_only",
        "read_only",
    ),
    (
        "a-read-without-a-diagnosis",
        lambda: asked(
            valid("list_failed_units", {"node": "db"}), diagnosis=NO_DIAGNOSIS, mode="diagnose_only"
        ),
        "allow",
        "read_only",
        "read_only",
    ),
    (
        "an-action-without-a-diagnosis",
        lambda: asked(valid("start_service", UNIT, META), diagnosis=NO_DIAGNOSIS),
        "deny",
        "diagnosis_required",
        "diagnosis_required_before_action",
    ),
    (
        "an-action-in-recommend-mode",
        lambda: asked(valid("start_service", UNIT, META), mode="recommend"),
        "deny",
        "recommend",
        "recommend_mode_not_executed",
    ),
    (
        "an-unsure-action",
        lambda: asked(valid("start_service", UNIT, {**META, "action_confidence": 0.2})),
        "deny",
        "low_risk_unsure_autonomous",
        "low_risk_below_thresholds",
    ),
    (
        "an-unknown-tool",
        lambda: asked(Judged(name="no_such_tool", known=False, offered=False, error=UNKNOWN_TOOL)),
        "deny",
        "tool_unknown",
        "tool_unknown",
    ),
    (
        "a-tool-that-is-not-offered",
        lambda: asked(Judged(name="docker_ps", known=True, offered=False, error=NOT_OFFERED)),
        "deny",
        "tool_not_offered",
        "tool_not_offered",
    ),
    (
        "a-forbidden-tool",
        lambda: asked(Judged(name="flush_firewall", known=True, offered=False, error="forbidden")),
        "deny",
        "tool_forbidden",
        "tool_forbidden",
    ),
    (
        "a-path-that-is-not-allowed",
        lambda: asked(refused("read_file", {"node": "web", "path": "/etc/shadow"})),
        "deny",
        "call_invalid",
        "invalid_call:path_not_allowed",
    ),
    (
        "the-orchestrators-read",
        lambda: asked(valid("list_failed_units", {"node": "db"}), caller="orchestrator"),
        "allow",
        "evidence_profile",
        "read_only_for_orchestrator",
    ),
    (
        "the-orchestrators-action",
        lambda: asked(valid("start_service", UNIT, META), caller="orchestrator"),
        "deny",
        "orchestrator_never_mutates",
        "orchestrator_never_mutates",
    ),
    (
        "the-orchestrators-action-that-is-not-offered",
        lambda: asked(
            Judged(name="start_service", known=True, offered=False, error=NOT_OFFERED),
            caller="orchestrator",
        ),
        "deny",
        "orchestrator_never_mutates",
        "orchestrator_never_mutates",
    ),
    (
        "the-orchestrators-action-that-is-not-valid",
        lambda: asked(refused("restart_service", {"node": "web"}), caller="orchestrator"),
        "deny",
        "orchestrator_never_mutates",
        "orchestrator_never_mutates",
    ),
    (
        "the-orchestrators-read-that-is-not-offered",
        lambda: asked(
            Judged(name="read_file", known=True, offered=False, error=NOT_OFFERED),
            caller="orchestrator",
        ),
        "deny",
        "tool_not_offered",
        "tool_not_offered",
    ),
    (
        "the-orchestrators-forbidden-tool",
        lambda: asked(
            Judged(name="flush_firewall", known=True, offered=False, error="forbidden"),
            caller="orchestrator",
        ),
        "deny",
        "tool_forbidden",
        "tool_forbidden",
    ),
]


@pytest.mark.parametrize(
    ("question", "effect", "rule_id", "reason"),
    [pytest.param(*case[1:], id=case[0]) for case in QUESTIONS],
)
def test_what_the_engine_decides_about_a_question_of_the_executors_making(
    engine: Opa, question: Any, effect: str, rule_id: str, reason: str
) -> None:
    decision = engine.decide(question())
    assert (decision.effect, decision.rule_id, decision.reason) == (effect, rule_id, reason)
    assert decision.bundle_hash == HASH


def test_every_tool_of_the_registry_is_known_to_the_bundle_at_its_version(engine: Opa) -> None:
    # The registry the executor loads and the data the bundle holds are one registry.
    for tool in REGISTRY.tools:
        question = asked(Judged(name=tool.name, known=True, offered=True, error="bad_form"))
        decision = engine.decide(question)
        expected = "tool_forbidden" if tool.risk == "forbidden" else "call_invalid"
        assert decision.rule_id == expected, tool.name
        # The bundle has the tool at exactly the registry's version: a call that names that
        # version is of a known tool, one that names the next or the last is not.
        for version, known in (
            (tool.version, True),
            (tool.version + 1, False),
            (tool.version - 1, False),
        ):
            other = dict(question)
            other["tool"] = {**question["tool"], "version": version}
            assert (engine.decide(other).rule_id == expected) is known, (tool.name, version)
            assert (engine.decide(other).rule_id == "tool_unknown") is not known, tool.name


def test_the_engine_refuses_every_path_but_the_decisions(opa_server: str) -> None:
    for path in ("potka/action", "potka/action/verdict", "tools/start_service", "system/authz"):
        client = Opa(opa_server, path, HASH)
        try:
            with pytest.raises(PolicyUnavailable) as refused_:
                client.decide({})
            assert (refused_.value.reason, str(refused_.value)) == (
                "refused",
                "the policy engine answered 401",
            )
        finally:
            client.close()


def test_what_the_real_engine_answers_to_a_request_the_client_never_makes(opa_server: str) -> None:
    url = f"{opa_server}/v1/data/{DECISION_PATH}"
    with httpx.Client(trust_env=False, timeout=5.0) as raw:
        # A slash after the decision's path is a redirect, which the client does not follow.
        assert raw.post(f"{url}/", json={"input": {}}).status_code == 301
        # A body that is no JSON is the caller's mistake.
        bad = raw.post(url, content=b'{"input":', headers={"content-type": "application/json"})
        assert (bad.status_code, bad.json()["code"]) == (400, "invalid_parameter")
        # A body without an input is decided, with no input: denied.
        bare = raw.post(url, json={})
        assert bare.json()["result"]["effect"] == "deny"
        assert bare.json()["warning"]["code"] == "api_usage_warning"
        # The bundle's data, its policies and the engine's own state are not served.
        for method, path in (
            ("GET", "/v1/data"),
            ("GET", "/v1/policies"),
            ("PUT", "/v1/data/tools/x"),
        ):
            refusal = raw.request(method, f"{opa_server}{path}", json={})
            assert (refusal.status_code, refusal.json()["code"]) == (401, "unauthorized"), path
        assert raw.get(f"{opa_server}/health").status_code == 200


def test_an_engine_whose_bundle_states_another_hash_is_told_apart(tmp_path: Path) -> None:
    other = tmp_path / "policy"
    shutil.copytree(BUNDLE, other)
    rego = other / "potka" / "action.rego"
    rego.write_text(rego.read_text(encoding="utf-8") + "\n# Another release.\n", encoding="utf-8")
    (other / HASH_FILE).write_text(hash_file(bundle_hash(other)), encoding="utf-8")
    assert bundle_hash(other) != HASH
    with server(other) as address:
        client = Opa(address, DECISION_PATH, HASH)
        try:
            with pytest.raises(AnotherBundle) as told:
                client.decide(
                    asked(valid("read_file", {"node": "web", "path": "/etc/nginx/nginx.conf"}))
                )
            assert told.value.decision.bundle_hash == bundle_hash(other)
            assert told.value.decision.effect == "allow"
            with pytest.raises(AnotherBundle):
                client.check()
        finally:
            client.close()
        own = Opa(address, DECISION_PATH, bundle_hash(other))
        try:
            own.check()
        finally:
            own.close()


def test_a_changed_bundle_that_still_states_the_old_hash_is_not_told_apart_by_a_decision(
    tmp_path: Path,
) -> None:
    # The hash is the bundle's own word. What a decision cannot show, the directory does:
    # the hash it states is no longer the hash of its files.
    other = tmp_path / "policy"
    shutil.copytree(BUNDLE, other)
    rego = other / "potka" / "action.rego"
    rego.write_text(rego.read_text(encoding="utf-8") + "\n# Changed in place.\n", encoding="utf-8")
    assert kept_hash(other) == HASH
    assert bundle_hash(other) != HASH
    with server(other) as address:
        client = Opa(address, DECISION_PATH, HASH)
        try:
            client.check()
            assert client.decide({}).bundle_hash == HASH
        finally:
            client.close()


def test_a_bundle_without_its_hash_file_decides_nothing_that_is_taken(tmp_path: Path) -> None:
    other = tmp_path / "policy"
    shutil.copytree(BUNDLE, other)
    (other / HASH_FILE).unlink()
    with server(other) as address:
        client = Opa(address, DECISION_PATH, HASH)
        try:
            with pytest.raises(PolicyUnavailable) as told:
                client.check()
            assert told.value.reason == "no_decision"
        finally:
            client.close()


def test_no_engine_at_the_address_is_unreachable() -> None:
    client = Opa("http://127.0.0.1:9", DECISION_PATH, HASH, timeout=2.0)
    try:
        with pytest.raises(PolicyUnavailable) as told:
            client.decide({})
        assert told.value.reason == "unreachable"
    finally:
        client.close()


def test_a_port_that_was_taken_meanwhile_is_not_the_end(monkeypatch: pytest.MonkeyPatch) -> None:
    free = testing_opa._free_port
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        taken = int(held.getsockname()[1])
        ports = iter([taken, taken, free()])
        monkeypatch.setattr(testing_opa, "_free_port", lambda: next(ports))
        with server() as address:
            assert not address.endswith(f":{taken}")
            client = Opa(address, DECISION_PATH, HASH)
            try:
                client.check()
            finally:
                client.close()
        # Never a free port: the error holds what the server printed, and no server is left.
        monkeypatch.setattr(testing_opa, "_free_port", lambda: taken)
        with pytest.raises(RuntimeError, match="OPA did not start"), server():
            pass
