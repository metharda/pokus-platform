"""A scripted Incus for the tests of the client: canned answers, recorded requests.

Every answer a test gives here goes through the comparison with the Incus
API extract (``potka_testing.incus_api``), so a test cannot script an
answer in a form Incus does not have.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
from potka_incus.client import Incus

from potka_testing.incus_api import checked

URL = "https://incus.invalid"
PROJECT = "bench"
OPERATION = str(uuid.UUID(int=7))
FINGERPRINT = "ab" * 32


def sync(metadata: Any) -> httpx.Response:
    """An answer given at once."""
    body = {"type": "sync", "status": "Success", "status_code": 200, "metadata": metadata}
    return httpx.Response(200, json=body)


def started(identifier: str = OPERATION) -> httpx.Response:
    """The announcement of an operation."""
    body = {
        "type": "async",
        "status": "Operation created",
        "status_code": 100,
        "operation": f"/1.0/operations/{identifier}",
        "metadata": {"id": identifier, "status": "Running", "status_code": 103},
    }
    return httpx.Response(202, json=body)


def ended(status_code: int = 200, **more: Any) -> httpx.Response:
    """An operation's record at the end of the wait for it."""
    status = {200: "Success", 400: "Failure", 401: "Cancelled"}.get(status_code, "Running")
    return sync({"id": OPERATION, "status": status, "status_code": status_code, **more})


def error(status: int, text: str) -> httpx.Response:
    """A refusal."""
    return httpx.Response(status, json={"type": "error", "error": text, "error_code": status})


class Script:
    """What Incus answers, call by call, and what it was asked."""

    def __init__(self) -> None:
        """Start with no answer and no request."""
        self.answers: dict[tuple[str, str], list[httpx.Response | Exception]] = {}
        self.requests: list[httpx.Request] = []

    def on(self, method: str, path: str, *answers: httpx.Response | Exception) -> Script:
        """Give the answers to the next requests of METHOD and PATH, in order."""
        self.answers.setdefault((method, path), []).extend(answers)
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record the request and give its next answer; fail on a request without one."""
        self.requests.append(request)
        answers = self.answers.get((request.method, request.url.path))
        assert answers, f"no answer scripted for {request.method} {request.url.path}"
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def asked(self) -> list[tuple[str, str, dict[str, str]]]:
        """Each request's method, path and query parameters."""
        return [(r.method, r.url.path, dict(r.url.params)) for r in self.requests]

    def body(self, index: int = -1) -> Any:
        """The JSON body of a request (the last by default)."""
        return json.loads(self.requests[index].content)

    def client(self, project: str = PROJECT) -> Incus:
        """A client whose requests this script answers, each compared with the extract."""
        return Incus(URL, project, transport=checked(httpx.MockTransport(self)))

    def bare(self, project: str = PROJECT) -> Incus:
        """A client without the comparison, for an answer that is none of Incus's."""
        return Incus(URL, project, transport=httpx.MockTransport(self))
