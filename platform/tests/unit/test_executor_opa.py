"""The client of the policy engine: only a decision of the release's bundle is taken.

No policy engine runs here. A transport of the test's own answers what a
healthy engine cannot be made to say; those answers are written for these
tests and are not recordings. What the real engine answers is in
``tests/integration/test_policy_opa.py``.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from potka_core.canonical import canonical_json
from potka_core.endpoints import Opa as OpaEndpoint
from potka_executor import opa as opa_module
from potka_executor.opa import MAX_ANSWER, TIMEOUT, AnotherBundle, Opa, PolicyUnavailable

HASH = "a" * 64
PATH = "potka/action/decision"
DECISION = {"effect": "allow", "rule_id": "read_only", "reason": "read_only", "bundle_hash": HASH}
ASKED: dict[str, Any] = {
    "caller": "agent",
    "tool": {"name": "read_file", "known": True, "offered": True},
}


def engine(answer: Callable[[httpx.Request], httpx.Response]) -> Opa:
    return Opa("http://127.0.0.1:8181", PATH, HASH, transport=httpx.MockTransport(answer))


def saying(status: int, body: object) -> Opa:
    content = body if isinstance(body, bytes) else json.dumps(body).encode()
    return engine(lambda request: httpx.Response(status, content=content))


def test_a_decision_of_the_releases_bundle_is_taken() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"result": DECISION})

    decision = engine(answer).decide(ASKED)
    assert (decision.effect, decision.rule_id, decision.reason) == (
        "allow",
        "read_only",
        "read_only",
    )
    assert decision.bundle_hash == HASH
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:8181/v1/data/potka/action/decision"
    assert json.loads(request.content) == {"input": ASKED}
    # The bytes sent are the canonical ones, which the input's hash is of.
    assert request.content == canonical_json({"input": ASKED})
    assert request.headers["content-type"] == "application/json"
    assert request.headers["accept-encoding"] == "identity"


def test_what_the_engine_adds_beside_the_result_is_ignored() -> None:
    answer = {"result": DECISION, "decision_id": "1", "warning": {"code": "api_usage_warning"}}
    assert saying(200, answer).decide(ASKED).effect == "allow"


def test_a_decision_of_another_bundle_is_raised_with_what_it_said() -> None:
    other = {**DECISION, "bundle_hash": "b" * 64}
    with pytest.raises(AnotherBundle) as refused:
        saying(200, {"result": other}).decide(ASKED)
    assert refused.value.reason == "another_bundle"
    assert refused.value.decision.bundle_hash == "b" * 64
    assert refused.value.decision.effect == "allow"
    assert isinstance(refused.value, PolicyUnavailable)


@pytest.mark.parametrize("status", [201, 204, 301, 302, 400, 401, 403, 404, 500, 503])
def test_any_status_but_200_is_no_decision(status: int) -> None:
    with pytest.raises(PolicyUnavailable) as refused:
        saying(status, {"result": DECISION}).decide(ASKED)
    assert refused.value.reason == "refused"
    assert str(refused.value) == f"the policy engine answered {status}"
    assert not isinstance(refused.value, AnotherBundle)


def test_a_redirect_is_not_followed() -> None:
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if len(asked) == 1:
            return httpx.Response(301, headers={"location": "http://127.0.0.1:8181/elsewhere"})
        return httpx.Response(200, json={"result": DECISION})

    with pytest.raises(PolicyUnavailable, match="answered 301"):
        engine(answer).decide(ASKED)
    assert asked == ["http://127.0.0.1:8181/v1/data/potka/action/decision"]


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json",
        b"[]",
        b"null",
        {},
        {"result": None},
        {"result": []},
        {"result": "allow"},
        {"result": {}},
        {"warning": {"code": "api_usage_warning"}},
        {"result": {**DECISION, "effect": "permit"}},
        {"result": {**DECISION, "effect": True}},
        {"result": {**DECISION, "rule_id": ""}},
        {"result": {**DECISION, "reason": "a reason in words"}},
        {"result": {**DECISION, "bundle_hash": ""}},
        {"result": {**DECISION, "bundle_hash": "A" * 64}},
        {"result": {**DECISION, "more": 1}},
        {"result": {key: value for key, value in DECISION.items() if key != "bundle_hash"}},
        {"result": {key: value for key, value in DECISION.items() if key != "effect"}},
        {"result": {key: value for key, value in DECISION.items() if key != "rule_id"}},
        {"result": {key: value for key, value in DECISION.items() if key != "reason"}},
    ],
)
def test_an_answer_that_is_not_exactly_a_decision_is_none(body: Any) -> None:
    with pytest.raises(PolicyUnavailable) as refused:
        saying(200, body).decide(ASKED)
    assert refused.value.reason == "no_decision"
    assert not isinstance(refused.value, AnotherBundle)
    # Neither the error of the JSON reader nor the one of the decision's form is carried:
    # each holds the answer.
    assert refused.value.__cause__ is None
    assert refused.value.__context__ is None


def test_a_bundle_without_its_hash_is_no_bundle_to_act_on() -> None:
    # A bundle whose hash file is missing answers an empty hash.
    with pytest.raises(PolicyUnavailable) as refused:
        saying(200, {"result": {**DECISION, "bundle_hash": ""}}).decide(ASKED)
    assert refused.value.reason == "no_decision"


def test_an_answer_larger_than_a_decision_can_be_is_not_read_to_its_end() -> None:
    padded = {"result": DECISION, "padding": "x" * MAX_ANSWER}
    with pytest.raises(PolicyUnavailable) as refused:
        saying(200, padded).decide(ASKED)
    assert (refused.value.reason, str(refused.value)) == (
        "no_decision",
        "the policy engine's answer is too large",
    )
    at_most = {"result": DECISION, "padding": ""}
    room = MAX_ANSWER - len(json.dumps(at_most).encode())
    at_most["padding"] = "x" * room
    assert len(json.dumps(at_most).encode()) == MAX_ANSWER
    assert saying(200, at_most).decide(ASKED).effect == "allow"


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("refused"),
        httpx.ConnectTimeout("slow"),
        httpx.ReadTimeout("slow"),
        httpx.RemoteProtocolError("cut"),
        httpx.ReadError("reset"),
    ],
)
def test_an_engine_that_does_not_answer_is_unreachable(error: httpx.HTTPError) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(PolicyUnavailable) as refused:
        engine(answer).decide(ASKED)
    assert refused.value.reason == "unreachable"
    assert str(refused.value) == f"the policy engine did not answer ({type(error).__name__})"
    # No other error is carried: the transport's holds the request, and with it the question.
    assert refused.value.__cause__ is None
    assert refused.value.__context__ is None


def test_an_answer_nested_deeper_than_it_can_be_read_is_none() -> None:
    deep = b"[" * 60000
    assert len(deep) < MAX_ANSWER
    with pytest.raises(PolicyUnavailable) as refused:
        saying(200, deep).decide(ASKED)
    assert (refused.value.reason, str(refused.value)) == (
        "no_decision",
        "the policy engine's answer is no JSON",
    )
    assert refused.value.__context__ is None


class Unread(httpx.SyncByteStream):
    """A body that must not be read."""

    def __iter__(self) -> Iterator[bytes]:
        raise AssertionError("the body was read")


@pytest.mark.parametrize("encoding", ["gzip", "GZIP", "deflate", "br", "gzip, identity"])
def test_an_encoded_answer_is_refused_before_any_of_it_is_unpacked(encoding: str) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-encoding": encoding}, stream=Unread())

    with pytest.raises(PolicyUnavailable) as refused:
        engine(answer).decide(ASKED)
    assert (refused.value.reason, str(refused.value)) == (
        "no_decision",
        "the policy engine's answer is encoded",
    )


def test_an_answer_that_says_it_is_not_encoded_is_read() -> None:
    packed = gzip.compress(json.dumps({"result": DECISION}).encode())

    def plain(request: httpx.Request) -> httpx.Response:
        content = json.dumps({"result": DECISION}).encode()
        return httpx.Response(200, headers={"content-encoding": "identity"}, content=content)

    assert engine(plain).decide(ASKED).effect == "allow"
    # Packed bytes without the header that says so are no JSON.
    with pytest.raises(PolicyUnavailable, match="is no JSON"):
        saying(200, packed).decide(ASKED)


class Clock:
    """A clock that a test moves."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Slow(httpx.SyncByteStream):
    """A body that comes in blocks, each after some seconds of the test's clock."""

    def __init__(self, clock: Clock, blocks: list[bytes], seconds: float) -> None:
        self.clock, self.blocks, self.seconds = clock, blocks, seconds
        self.given = 0

    def __iter__(self) -> Iterator[bytes]:
        for block in self.blocks:
            self.clock.now += self.seconds
            self.given += 1
            yield block


def test_an_answer_that_is_not_whole_in_time_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(opa_module, "monotonic", clock)
    whole = json.dumps({"result": DECISION}).encode()
    blocks = [whole[index : index + 20] for index in range(0, len(whole), 20)]
    assert len(blocks) > 3

    def answering(seconds: float) -> tuple[Opa, Slow]:
        body = Slow(clock, blocks, seconds)
        return engine(lambda request: httpx.Response(200, stream=body)), body

    # Every block comes well within the time one read has, and the whole does not.
    slow, body = answering(TIMEOUT * 0.4)
    with pytest.raises(PolicyUnavailable) as refused:
        slow.decide(ASKED)
    assert (refused.value.reason, str(refused.value)) == (
        "unreachable",
        "the policy engine did not answer in time",
    )
    assert body.given == 3
    quick, body = answering(TIMEOUT / (len(blocks) + 1))
    assert quick.decide(ASKED).effect == "allow"
    assert body.given == len(blocks)


def test_an_answer_whose_head_comes_too_late_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(opa_module, "monotonic", clock)

    def answer(request: httpx.Request) -> httpx.Response:
        clock.now += TIMEOUT + 0.1
        return httpx.Response(200, stream=Unread())

    with pytest.raises(PolicyUnavailable, match="did not answer in time"):
        engine(answer).decide(ASKED)


@pytest.mark.parametrize(
    "value",
    [
        {"meta": {"action_confidence": float("nan")}},
        {"meta": {"action_confidence": float("inf")}},
        {"args": {"path": b"/etc"}},
        {"args": {1: "a"}},
        {"counters": {"steps": 2**53 + 1}},
    ],
)
def test_an_input_that_is_no_json_is_refused_and_the_engine_is_not_asked(value: Any) -> None:
    asked: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(200, json={"result": DECISION})

    with pytest.raises(ValueError) as refused:
        engine(answer).decide(value)
    assert not isinstance(refused.value, PolicyUnavailable)
    assert asked == []


def test_no_message_repeats_the_question_or_the_answer() -> None:
    marker = "the-arguments-of-a-call"
    for said in (
        saying(500, {"error": marker}),
        saying(200, {"result": {**DECISION, "reason": marker + " in words"}}),
        saying(200, marker.encode()),
    ):
        with pytest.raises(PolicyUnavailable) as refused:
            said.decide({"args": {"path": marker}})
        assert marker not in str(refused.value)


def test_the_client_takes_no_proxy_from_the_environment_and_follows_no_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.org:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.example.org:3128")
    client = Opa("http://127.0.0.1:8181", PATH, HASH)
    try:
        inner = client._client
        assert inner.trust_env is False
        assert inner.follow_redirects is False
        assert inner.timeout == httpx.Timeout(TIMEOUT)
        assert TIMEOUT == 5.0
    finally:
        client.close()


def test_the_check_of_readiness_asks_about_nothing_and_must_be_denied() -> None:
    asked: list[Any] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(json.loads(request.content))
        denied = {**DECISION, "effect": "deny", "rule_id": "tool_unknown", "reason": "tool_unknown"}
        return httpx.Response(200, json={"result": denied})

    engine(answer).check()
    assert asked == [{"input": {}}]


def test_a_bundle_that_allows_an_empty_question_is_not_ready() -> None:
    with pytest.raises(PolicyUnavailable) as refused:
        saying(200, {"result": DECISION}).check()
    assert (refused.value.reason, str(refused.value)) == (
        "no_decision",
        "the bundle does not deny an empty question",
    )
    with pytest.raises(AnotherBundle):
        saying(200, {"result": {**DECISION, "effect": "deny", "bundle_hash": "b" * 64}}).check()


@pytest.mark.parametrize(
    ("url", "path", "bundle_hash"),
    [
        ("ftp://127.0.0.1:8181", PATH, HASH),
        ("127.0.0.1:8181", PATH, HASH),
        ("http://127.0.0.1:8181/v1", PATH, HASH),
        ("http://", PATH, HASH),
        ("http://[::1", PATH, HASH),
        ("http://127.0.0.1:99999", PATH, HASH),
        ("http://127.0.0.1:0", PATH, HASH),
        ("http://127.0.0.1:8181?explain=full", PATH, HASH),
        ("http://127.0.0.1:8181/?pretty=true", PATH, HASH),
        ("http://user:word@127.0.0.1:8181", PATH, HASH),
        ("http://user@127.0.0.1:8181", PATH, HASH),
        ("http://127.0.0.1:8181#part", PATH, HASH),
        ("http://127.0.0.1:8181", "potka", HASH),
        ("http://127.0.0.1:8181", "potka/action/decision/", HASH),
        ("http://127.0.0.1:8181", "/potka/action/decision", HASH),
        ("http://127.0.0.1:8181", "potka/../system/authz", HASH),
        ("http://127.0.0.1:8181", "potka/action/decision?pretty=true", HASH),
        ("http://127.0.0.1:8181", "Potka/Action", HASH),
        ("http://127.0.0.1:8181", PATH, "a" * 63),
        ("http://127.0.0.1:8181", PATH, "A" * 64),
        ("http://127.0.0.1:8181", PATH, ""),
    ],
)
def test_an_address_a_path_or_a_hash_of_another_form_is_refused(
    url: str, path: str, bundle_hash: str
) -> None:
    with pytest.raises(ValueError):
        Opa(url, path, bundle_hash)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8181",
        "http://127.0.0.1:8181/",
        "https://opa.example.org",
        "http://[::1]:8181",
    ],
)
def test_an_address_of_a_host_and_a_port_alone_is_taken(url: str) -> None:
    client = Opa(url, PATH, HASH)
    try:
        assert client._url.path == "/v1/data/potka/action/decision"
        assert (client._url.query, client._url.userinfo, client._url.fragment) == (b"", b"", "")
    finally:
        client.close()


def test_the_engine_is_the_one_the_endpoints_name() -> None:
    endpoint = OpaEndpoint(url="http://127.0.0.1:18181", decision_path="potka/action/decision")
    client = Opa.of(endpoint, HASH)
    try:
        assert str(client._url) == "http://127.0.0.1:18181/v1/data/potka/action/decision"
        assert client.bundle_hash == HASH
    finally:
        client.close()
    with pytest.raises(ValueError, match="no policy engine with a decision path"):
        Opa.of(None, HASH)
    with pytest.raises(ValueError, match="no policy engine with a decision path"):
        Opa.of(OpaEndpoint(url="http://127.0.0.1:18181"), HASH)
