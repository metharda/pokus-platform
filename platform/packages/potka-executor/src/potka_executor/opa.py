"""Asking the policy engine about a call, and what counts as its decision.

The engine (OPA) is asked with one request for every call: the policy
input, sent to the bundle's decision path as its canonical JSON, the bytes
the input's hash is of. Its answer is a decision only when it is exactly
one: status 200, a JSON object whose ``result`` holds an effect of the
three, a rule's ID, a reason and the hash of the bundle that made it.
Anything else is no decision, and nothing is done on it: no whole answer in
time, another status (the engine's authorization refuses every path but the
decision's), a redirect, a body that is encoded, no JSON or too large, a
result of another form.

A decision says which bundle made it by the hash that bundle states for
itself. A decision that states another hash than the release's must not be
acted on: the engine serves a bundle this release does not hold, as after a
deployment the engine has not followed. It is raised apart, with the
decision, so that what was answered can be recorded with the hash it
carried. The hash is the bundle's own word: it tells the bundle of another
release apart, not a directory whose files were changed while its hash
file was left as it was. That the directory the engine loads is the
release's own is the lab's part, which gives a release to its engine
read-only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from time import monotonic
from typing import Any, Final, Literal

import httpx
from potka_core.canonical import canonical_json
from potka_core.endpoints import Opa as OpaEndpoint
from pydantic import ValidationError

from potka_executor.policy import Decision

TIMEOUT: Final = 5.0
"""The seconds the engine has for one answer: an answer that is not whole
that long after it was asked for is none, and no step of it (connecting,
sending, one read) is waited for longer."""
MAX_ANSWER: Final = 65536
"""The most bytes of an answer that are read: a decision is a few hundred."""
DECISION_PATH_RE: Final = re.compile(r"[a-z][a-z0-9_]*(/[a-z][a-z0-9_]*)+")
SHA256_RE: Final = re.compile(r"[0-9a-f]{64}")
ADDRESS: Final = "the policy engine's address is an http(s) URL of a host and a port alone"
_NO_JSON: Final = object()

Reason = Literal["unreachable", "refused", "no_decision", "another_bundle"]


class PolicyUnavailable(Exception):
    """No decision of the release's bundle was had, so nothing may be done.

    Attributes:
        reason: Why: the engine gave no whole answer in time
            (``unreachable``), answered another status than 200
            (``refused``), answered what is no decision (``no_decision``),
            or decided with a bundle that states another hash than the
            release's (``another_bundle``).

    The error carries no other error with it, neither as its cause nor as
    its context: the ones it stands for hold the question or the answer.
    """

    def __init__(self, reason: Reason, message: str) -> None:
        """Keep the reason; the message names no part of the question."""
        super().__init__(message)
        self.reason: Reason = reason


class AnotherBundle(PolicyUnavailable):
    """The engine decided with a bundle that states another hash than the release's.

    Attributes:
        decision: What it answered, with the hash it carried.
    """

    def __init__(self, decision: Decision) -> None:
        """Keep the decision."""
        super().__init__(
            "another_bundle", "the policy engine serves another bundle than the release's"
        )
        self.decision = decision


class Opa:
    """The policy engine's decision endpoint, for one bundle."""

    def __init__(
        self,
        url: str,
        decision_path: str,
        bundle_hash: str,
        *,
        timeout: float = TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Ask the engine at URL for the decision at DECISION_PATH.

        Args:
            url: The engine's address (``http://127.0.0.1:8181``).
            decision_path: The decision's path below ``/v1/data``
                (``potka/action/decision``).
            bundle_hash: The hash of the release's bundle: the only
                bundle whose decisions are taken.
            timeout: The seconds the engine has for one answer.
            transport: Another transport than the network's, for a test.

        Raises:
            ValueError: The URL is no ``http`` or ``https`` address of a
                host and a port alone (a path, a query, a user or a
                fragment is refused), the decision path has not its form,
                or the hash is no SHA-256 in hexadecimal.
        """
        try:
            address = httpx.URL(url)
        except httpx.InvalidURL:
            address = None
        if (
            address is None
            or address.scheme not in ("http", "https")
            or not address.host
            or address.path not in ("", "/")
            or address.query
            or address.userinfo
            or address.fragment
            or (address.port is not None and not 0 < address.port < 65536)
        ):
            raise ValueError(ADDRESS)
        if DECISION_PATH_RE.fullmatch(decision_path) is None:
            raise ValueError("the decision path is lowercase words joined by slashes, two at least")
        if SHA256_RE.fullmatch(bundle_hash) is None:
            raise ValueError("the bundle's hash is a sha256 in hexadecimal")
        self.bundle_hash = bundle_hash
        self._timeout = timeout
        self._url = address.copy_with(path=f"/v1/data/{decision_path}")
        # The environment's proxies are not for a request to the lab's own engine. The answer
        # is asked for as it is: an encoded one would be unpacked before its size is known.
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            headers={"accept-encoding": "identity", "content-type": "application/json"},
        )

    @classmethod
    def of(cls, endpoint: OpaEndpoint | None, bundle_hash: str) -> Opa:
        """The engine the lab's endpoints name.

        Args:
            endpoint: The endpoints' ``opa`` table, or None without one.
            bundle_hash: The hash of the release's bundle.

        Raises:
            ValueError: The endpoints name no engine or no decision path.
        """
        if endpoint is None or endpoint.decision_path is None:
            raise ValueError("the endpoints name no policy engine with a decision path")
        return cls(endpoint.url, endpoint.decision_path, bundle_hash)

    def close(self) -> None:
        """Close the connections."""
        self._client.close()

    def decide(self, value: Mapping[str, Any]) -> Decision:
        """Ask about one call.

        Args:
            value: The policy input.

        Returns:
            The decision of the release's bundle.

        Raises:
            AnotherBundle: The engine decided, with another bundle.
            PolicyUnavailable: There is no decision (the class's reasons).
            ValueError: VALUE has no canonical JSON form (NaN, a type JSON
                does not have); the engine is not asked.
        """
        question = canonical_json({"input": value})
        body = self._answer(question)
        try:
            answer = json.loads(body)
        except (ValueError, RecursionError):
            # No JSON, or JSON nested deeper than it can be read.
            answer = _NO_JSON
        if answer is _NO_JSON:
            raise PolicyUnavailable("no_decision", "the policy engine's answer is no JSON")
        result = answer.get("result") if isinstance(answer, dict) else None
        if not isinstance(result, dict):
            raise PolicyUnavailable("no_decision", "the policy engine's answer holds no result")
        decision: Decision | None
        try:
            decision = Decision.model_validate(result)
        except ValidationError:
            # Raised below, outside the handler: the validation error holds the answer.
            decision = None
        if decision is None:
            raise PolicyUnavailable("no_decision", "the policy engine's result is no decision")
        if decision.bundle_hash != self.bundle_hash:
            raise AnotherBundle(decision)
        return decision

    def _answer(self, question: bytes) -> bytes:
        """The engine's answer to QUESTION: the body of a status 200, whole and in time."""
        deadline = monotonic() + self._timeout
        late = PolicyUnavailable("unreachable", "the policy engine did not answer in time")
        failed: PolicyUnavailable | None = None
        body = b""
        try:
            with self._client.stream("POST", self._url, content=question) as response:
                if response.status_code != 200:
                    raise PolicyUnavailable(
                        "refused", f"the policy engine answered {response.status_code}"
                    )
                encoding = response.headers.get("content-encoding", "identity").strip().lower()
                if encoding not in ("", "identity"):
                    raise PolicyUnavailable("no_decision", "the policy engine's answer is encoded")
                if monotonic() > deadline:
                    raise late
                for block in response.iter_bytes():
                    body += block
                    if len(body) > MAX_ANSWER:
                        raise PolicyUnavailable(
                            "no_decision", "the policy engine's answer is too large"
                        )
                    if monotonic() > deadline:
                        raise late
        except httpx.HTTPError as exc:
            # Made here and raised below, outside the handler: the httpx error holds the
            # request, and with it the question.
            failed = PolicyUnavailable(
                "unreachable", f"the policy engine did not answer ({type(exc).__name__})"
            )
        if failed is not None:
            raise failed
        return body

    def check(self) -> None:
        """Whether the engine's bundle states the release's hash and denies what it does not know.

        The engine is asked about nothing at all. A bundle that fails
        closed denies it; one that allows it is no bundle to act on.

        Raises:
            AnotherBundle: The engine's bundle states another hash.
            PolicyUnavailable: There is no decision, or the empty question
                was not denied.
        """
        decision = self.decide({})
        if decision.effect != "deny":
            raise PolicyUnavailable("no_decision", "the bundle does not deny an empty question")
