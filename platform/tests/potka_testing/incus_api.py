"""Requests to Incus and its answers, compared with the Incus API extract.

``tests/fixtures/incus-api/api.json`` says, for every call of the Incus
client, what Incus itself describes: the query parameters, the headers, the
response codes and the structure of the bodies. :class:`Api` reads it and
names what a request or a response has that the description lacks: another
call, another parameter or header, a parameter given twice, another status,
a key no definition names, a value of another type, a configuration key
Incus does not have.

What the description cannot say, the comparison cannot find: it marks no
field as required, so a missing field is not found; a ``format`` (the range
of a number, the form of a date) is not compared; the bytes of a raw body
are not read; and a ``metadata`` that an answer's own schema does not
describe is free (an operation's, a refusal's).

Six readings go beyond the description's letter, each for a reason in
Incus itself:

* A definition is taken as closed: it is made from Incus's own types, so
  a key it does not list is a difference.
* An answer's envelope has seven keys whatever its kind (:data:`ENVELOPE`),
  of which the description lists some for each answer; any of the seven
  is taken, with its type.
* An answer the description leaves raw is compared when it is JSON all the
  same: it is the names in a directory, in an envelope whose ``metadata``
  is a list of names.
* The description does not list every refusal of a call (a missing
  instance is answered with 404, which the call that reads an instance
  does not list): a refusal with a status the call does not list is
  compared with the form of the refusals it does list.
* Incus is written in Go, which sends a list or a map that was never set
  as ``null``, and a value it holds by reference (:data:`POINTERS`): a
  null there is not a difference. Anywhere else it is, an object with
  described properties included.
* An instance's configuration also holds its image's properties as
  ``image.*`` keys, which the configuration's own list does not have.

Devices are compared as the platform gives them: a ``disk`` and a ``nic``
on a managed network; any other type is a difference.

:func:`checked` wraps a transport so that every request a test makes
through it, and every answer, is compared; the first difference fails the
test with all that was found.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx

EXTRACT = Path(__file__).resolve().parents[1] / "fixtures" / "incus-api" / "api.json"
PREFIX = "x-incus-"
NO_BODY = "no JSON body, and the description has one"
FIRST_REFUSAL = 400
REF = "#/definitions/"
TYPES: dict[str, type | tuple[type, ...]] = {
    "array": list,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
    "object": dict,
    "string": str,
}
# The keys of every answer of Incus and their types (its shared/api/response.go); the
# metadata is whatever the call gives.
ENVELOPE = {
    "type": "string",
    "status": "string",
    "status_code": "integer",
    "operation": "string",
    "error_code": "integer",
    "error": "string",
    "metadata": "",
}
# The fields of the client's calls that Incus holds by reference: null when not set. Each
# is a property of one definition.
POINTERS = ("InstanceState.os_info",)
# The keys of an image's properties in an instance's configuration.
IMAGE_KEYS = "image.*"
# A device's type names the list of its keys.
DEVICE_KEYS = {"disk": "disk", "nic": "nic_bridged"}
# The two maps whose keys the configuration lists name, not a definition's properties.
MAPS = ("ConfigMap", "DevicesMap")


def _pattern(key: str) -> re.Pattern[str]:
    """A configuration key of the lists as a pattern: ``*`` and ``<name>`` stand for text."""
    return re.compile(re.escape(key).replace(r"\*", ".+").replace("<name>", "[^.]+"))


class Api:
    """The extract, and what it finds wrong with a request or a response."""

    def __init__(self, extract: dict[str, Any] | None = None) -> None:
        """Read the extract (the committed one when none is given)."""
        data = extract or json.loads(EXTRACT.read_text(encoding="utf-8"))
        self.calls: dict[str, Any] = data["calls"]
        self.definitions: dict[str, Any] = data["definitions"]
        self.config: dict[str, Any] = data["config"]
        self._routes = [
            (key, re.compile(re.sub(r"\\\{[a-z]+\\\}", "[^/]+", re.escape(key))))
            for key in self.calls
        ]
        self._instance_keys = [_pattern(key) for key in (*self.config["instance"], IMAGE_KEYS)]
        self._device_keys = {
            kind: [_pattern(key) for key in ("type", *self.config["devices"][listed])]
            for kind, listed in DEVICE_KEYS.items()
        }

    def call(self, method: str, path: str) -> str | None:
        """The call a request makes, as the extract names it, or None."""
        found = [key for key, route in self._routes if route.fullmatch(f"{method} {path}")]
        return found[0] if len(found) == 1 else None

    def structure(
        self, schema: Any, value: Any, where: str = "body", key: str = "", owner: str = ""
    ) -> list[str]:
        """What VALUE has that SCHEMA does not describe.

        Args:
            schema: A schema of the extract.
            value: A JSON value.
            where: The value's place, for the findings.
            key: The property the value is, as ``Definition.property``, when
                it is a property of a definition.
            owner: The definition SCHEMA is, when it is one.

        Returns:
            One line for each key no definition names, each value of
            another type (a null among them, but for a list, a map or a
            field of :data:`POINTERS`), and each configuration key Incus
            does not have.
        """
        if not schema:
            return []
        name = schema.get("$ref", "").removeprefix(REF)
        if name in MAPS:
            if value is None:
                return []
            return self._config(value, where) if name == MAPS[0] else self._devices(value, where)
        if name:
            return self.structure(self.definitions[name], value, where, key, name)
        kind = schema.get("type", "")
        if value is None and (
            kind == "array" or (kind == "object" and "properties" not in schema) or key in POINTERS
        ):
            return []
        expected = TYPES.get(kind)
        # In Python a boolean is an integer too; in JSON it is not.
        if expected and (
            not isinstance(value, expected) or isinstance(value, bool) != (expected is bool)
        ):
            return [f"{where}: {type(value).__name__}, not {kind}"]
        found: list[str] = []
        if isinstance(value, dict):
            properties = schema.get("properties")
            for member, item in value.items():
                if properties is not None and member in properties:
                    described = properties[member]
                    place = f"{owner}.{member}" if owner else ""
                    found += self.structure(described, item, f"{where}.{member}", place)
                elif "additionalProperties" in schema:
                    more = schema["additionalProperties"]
                    found += self.structure(more, item, f"{where}.{member}")
                elif properties is not None:
                    found.append(f"{where}.{member}: no such key in the description")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                found += self.structure(schema.get("items"), item, f"{where}[{index}]")
        return found

    def _config(self, value: Any, where: str) -> list[str]:
        if not isinstance(value, dict):
            return [f"{where}: {type(value).__name__}, not object"]
        found = []
        for key, item in value.items():
            if not any(pattern.fullmatch(key) for pattern in self._instance_keys):
                found.append(f"{where}.{key}: no such configuration key of an instance")
            elif not isinstance(item, str):
                found.append(f"{where}.{key}: not a string")
        return found

    def _devices(self, value: Any, where: str) -> list[str]:
        if not isinstance(value, dict):
            return [f"{where}: {type(value).__name__}, not object"]
        found = []
        for name, device in value.items():
            kind = device.get("type") if isinstance(device, dict) else None
            if kind not in DEVICE_KEYS:
                found.append(f"{where}.{name}: a device of type {kind!r}, which the extract lacks")
                continue
            for key, item in device.items():
                if not any(pattern.fullmatch(key) for pattern in self._device_keys[kind]):
                    found.append(f"{where}.{name}.{key}: no key of a {kind} device in the extract")
                elif not isinstance(item, str):
                    found.append(f"{where}.{name}.{key}: not a string")
        return found

    def _answer(self, schema: Any, body: Any, where: str) -> list[str]:
        """What an answer's JSON body has that Incus's envelope and SCHEMA do not."""
        if not isinstance(body, dict):
            return [f"{where}: {type(body).__name__}, not object"]
        described = (schema or {}).get("properties", {})
        found: list[str] = []
        rest = {}
        for key, item in body.items():
            if key in described or key not in ENVELOPE:
                rest[key] = item
            else:
                found += self.structure({"type": ENVELOPE[key]}, item, f"{where}.{key}")
        if schema:
            return found + self.structure(schema, rest, where)
        # The one answer the description leaves raw that is JSON: the names in a directory.
        names = body.get("metadata")
        if names is not None and not (
            isinstance(names, list) and all(isinstance(name, str) for name in names)
        ):
            found.append(f"{where}.metadata: not a list of names")
        return found + [f"{where}.{key}: no such key in an answer of Incus" for key in rest]

    def request(
        self, method: str, path: str, query: Iterable[str], headers: Iterable[str], body: Any
    ) -> list[str]:
        """What a request has that the description lacks.

        Args:
            method: The method.
            path: The path, without the query.
            query: The names of the query parameters, each as often as it is given.
            headers: The names of the headers (only Incus's own are compared).
            body: The JSON body, bytes for a raw body, or None.

        Returns:
            The findings; one alone when the call itself is not described.
        """
        key = self.call(method, path)
        if key is None:
            return [f"{method} {path}: no such call in the description"]
        call = self.calls[key]
        names = list(query)
        found = [
            f"{key}: query parameter {q} is not described"
            for q in dict.fromkeys(names)
            if q not in call["query"]
        ]
        found += [
            f"{key}: query parameter {q} is given twice"
            for q in dict.fromkeys(names)
            if names.count(q) > 1
        ]
        known = {name.lower() for name in call["headers"]}
        found += [
            f"{key}: header {h} is not described"
            for h in headers
            if h.lower().startswith(PREFIX) and h.lower() not in known
        ]
        expected = call["body"]
        if isinstance(expected, dict) and body is None:
            found.append(f"{key}: {NO_BODY}")
        elif isinstance(expected, dict):
            found += [f"{key}: {f}" for f in self.structure(expected, body, "request")]
        elif expected == "raw" and not isinstance(body, bytes):
            found.append(f"{key}: the description has raw body")
        elif expected is None and body not in (None, b""):
            found.append(f"{key}: the description has no body")
        return found

    def response(self, key: str, status: int, headers: Iterable[str], body: Any) -> list[str]:
        """What a response to the call KEY has that the description lacks.

        Args:
            key: The call, as :meth:`call` names it.
            status: The status code.
            headers: The names of the headers (only Incus's own are compared).
            body: The JSON body, or None when the body is not JSON.

        Returns:
            The findings.
        """
        responses = self.calls[key]["responses"]
        described = responses.get(str(status))
        if described is None and status >= FIRST_REFUSAL:
            # A refusal the call does not list has the form of those it lists.
            described = next((r for code, r in sorted(responses.items()) if code >= "400"), None)
        if described is None:
            return [f"{key}: status {status} is not described"]
        known = {name.lower() for name in described["headers"]}
        found = [
            f"{key}: response header {h} is not described"
            for h in headers
            if h.lower().startswith(PREFIX) and h.lower() not in known
        ]
        if described["schema"] is not None and body is None:
            found.append(f"{key}: {status}: {NO_BODY}")
        elif body is not None:
            # A body the description leaves raw may be JSON all the same (the names in a
            # directory): then it is an answer of Incus like any other.
            found += [f"{key}: {f}" for f in self._answer(described["schema"], body, f"{status}")]
        return found


def _json(content: bytes, what: str) -> Any:
    try:
        return json.loads(content)
    except ValueError:
        raise AssertionError([f"{what}: a JSON content type, and a body that is no JSON"]) from None


def checked(inner: httpx.BaseTransport, api: Api | None = None) -> httpx.MockTransport:
    """A transport that compares all it carries with the extract.

    Args:
        inner: The transport that answers.
        api: The extract (the committed one when None).

    Returns:
        A transport that fails the test (AssertionError) on a request or a
        response the description does not have, and otherwise hands on what
        INNER answered, unchanged.
    """
    extract = api or Api()

    def handle(request: httpx.Request) -> httpx.Response:
        what = f"{request.method} {request.url.path}"
        raw = request.content
        typed = "json" in request.headers.get("content-type", "")
        key = extract.call(request.method, request.url.path)
        if key is None:
            raise AssertionError([f"{what}: no such call in the description"])
        # A content type alone is no body.
        body: Any = _json(raw, what) if typed and raw else raw or None
        if body is None and extract.calls[key]["body"] == "raw":
            body = b""  # an empty file
        names = [name for name, _ in request.url.params.multi_items()]
        found = extract.request(request.method, request.url.path, names, request.headers, body)
        assert not found, found
        response = inner.handle_request(request)
        content = response.read()
        answered = "json" in response.headers.get("content-type", "")
        found = extract.response(
            key,
            response.status_code,
            response.headers,
            _json(content, f"{what}: {response.status_code}") if answered else None,
        )
        assert not found, found
        return response

    return httpx.MockTransport(handle)
