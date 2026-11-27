"""The comparison with the Incus API extract finds what the description lacks."""

from __future__ import annotations

import copy
import json
from typing import Any

import httpx
import pytest
from potka_testing.incus_api import FIRST_REFUSAL, Api, checked

API = Api()
STATE_CALL = "GET /1.0/instances/{name}/state"
EXEC = "/1.0/instances/web/exec"
FILES = "/1.0/instances/web/files"


def state(**disk: Any) -> dict[str, Any]:
    return {
        "status": "Running",
        "status_code": 103,
        "pid": 4242,
        "disk": {"root": {"usage": 1024, "total": 4096, **disk}},
    }


def envelope(metadata: Any) -> dict[str, Any]:
    return {"type": "sync", "status": "Success", "status_code": 200, "metadata": metadata}


def instance(config: dict[str, Any], devices: dict[str, Any]) -> list[str]:
    value = {"name": "web", "expanded_config": config, "expanded_devices": devices}
    return API.structure({"$ref": "#/definitions/Instance"}, value)


def test_a_request_is_matched_with_one_call_of_the_description() -> None:
    assert API.call("GET", "/1.0/instances") == "GET /1.0/instances"
    assert API.call("GET", "/1.0/instances/web") == "GET /1.0/instances/{name}"
    assert API.call("GET", "/1.0/instances/web/state") == STATE_CALL
    assert (
        API.call("DELETE", "/1.0/instances/web/logs/exec-output/exec_1.stdout")
        == "DELETE /1.0/instances/{name}/logs/exec-output/{filename}"
    )
    assert API.call("GET", "/1.0/operations/abc/wait") == "GET /1.0/operations/{id}/wait"
    for method, path in (
        ("PATCH", "/1.0/instances/web"),
        ("GET", "/1.0/instances/web/state/more"),
        ("GET", "/1.0/instances/a/b"),
        ("GET", "/1.0/images"),
        ("GET", "/1x0/instances"),
    ):
        assert API.call(method, path) is None
        assert API.request(method, path, [], [], None) == [
            f"{method} {path}: no such call in the description"
        ]


@pytest.mark.parametrize(
    ("value", "found"),
    [
        (state(), []),
        (state(usage="1024"), ["body.disk.root.usage: str, not integer"]),
        (state(usage=True), ["body.disk.root.usage: bool, not integer"]),
        (state(usage=1.5), ["body.disk.root.usage: float, not integer"]),
        (state(useage=1), ["body.disk.root.useage: no such key in the description"]),
        ({**state(), "extra": 1}, ["body.extra: no such key in the description"]),
        ({**state(), "disk": []}, ["body.disk: list, not object"]),
        ({**state(), "status": None}, ["body.status: NoneType, not string"]),
        ({**state(), "pid": None}, ["body.pid: NoneType, not integer"]),
        ({**state(), "pid": 1.0}, ["body.pid: float, not integer"]),
        # Through a reference to what is no object.
        ({**state(), "status_code": None}, ["body.status_code: NoneType, not integer"]),
        # A map or a list that was never set, and a value Incus holds by reference: null.
        ({**state(), "disk": None}, []),
        ({**state(), "network": None}, []),
        ({**state(), "network": {"eth0": {"addresses": None}}}, []),
        ({**state(), "os_info": None}, []),
        # Only the null: what is there is compared like any other object.
        ({**state(), "os_info": {"os": 1}}, ["body.os_info.os: int, not string"]),
        # An object with described properties is never null, at any depth.
        ({**state(), "cpu": None}, ["body.cpu: NoneType, not object"]),
        ({**state(), "disk": {"root": None}}, ["body.disk.root: NoneType, not object"]),
        (
            {**state(), "network": {"eth0": {"addresses": [None]}}},
            ["body.network.eth0.addresses[0]: NoneType, not object"],
        ),
        (None, ["body: NoneType, not object"]),
        ([], ["body: list, not object"]),
    ],
)
def test_a_body_has_the_keys_and_types_of_its_definition(value: Any, found: list[str]) -> None:
    assert API.structure({"$ref": "#/definitions/InstanceState"}, value) == found


def test_a_free_form_map_and_a_list_are_followed_as_far_as_they_are_described() -> None:
    operation = {"id": "a", "status_code": 200, "metadata": {"return": 0, "output": {"1": "/x"}}}
    assert API.structure({"$ref": "#/definitions/Operation"}, operation) == []
    assert API.structure({"$ref": "#/definitions/Operation"}, {"metadata": []}) == [
        "body.metadata: list, not object"
    ]
    created = {"name": "web", "profiles": ["target-container", 7]}
    assert API.structure({"$ref": "#/definitions/InstancesPost"}, created) == [
        "body.profiles[1]: int, not string"
    ]
    assert API.structure({"$ref": "#/definitions/InstancesPost"}, {"profiles": None}) == []
    assert API.structure({"$ref": "#/definitions/InstancesPost"}, {"profiles": [None]}) == [
        "body.profiles[0]: NoneType, not string"
    ]
    assert API.structure(None, {"anything": 1}) == []


def test_configuration_keys_and_device_keys_are_those_incus_has() -> None:
    config = {
        "security.nesting": "true",
        "limits.cpu": "2-3",
        "volatile.base_image": "ab" * 32,
        "volatile.eth0.hwaddr": "00:00:5e:00:53:01",
        "user.note": "x",
    }
    devices = {
        "root": {"type": "disk", "path": "/", "pool": "pinka-targets", "size": "8GiB"},
        "eth0": {"type": "nic", "network": "r-abc", "name": "eth0"},
    }
    assert instance(config, devices) == []
    # An image's properties are keys of the instance's configuration; a device key with a
    # wildcard stands for any key of that form.
    assert instance({"image.os": "Debian", "image.description": "a text"}, {}) == []
    initial = {"type": "disk", "path": "/", "pool": "p", "initial.uid": "0"}
    assert instance({}, {"root": initial}) == []
    bridged = {"type": "nic", "nictype": "bridged", "parent": "br0"}
    assert instance({"images.os": "x"}, {"eth0": bridged}) == [
        "body.expanded_config.images.os: no such configuration key of an instance",
        "body.expanded_devices.eth0.nictype: no key of a nic device in the extract",
    ]
    assert instance({"security.nestin": "true", "limits.cpu": 2, "volatile.hwaddr": "x"}, {}) == [
        "body.expanded_config.security.nestin: no such configuration key of an instance",
        "body.expanded_config.limits.cpu: not a string",
        "body.expanded_config.volatile.hwaddr: no such configuration key of an instance",
    ]
    assert instance({}, {"root": {"type": "disk", "sise": "8GiB", "size": 8}}) == [
        "body.expanded_devices.root.sise: no key of a disk device in the extract",
        "body.expanded_devices.root.size: not a string",
    ]
    assert instance(
        {}, {"gpu0": {"type": "gpu"}, "bare": "x", "eth0": {"type": "nic", "size": "1"}}
    ) == [
        "body.expanded_devices.gpu0: a device of type 'gpu', which the extract lacks",
        "body.expanded_devices.bare: a device of type None, which the extract lacks",
        "body.expanded_devices.eth0.size: no key of a nic device in the extract",
    ]
    assert instance([], "x") == [  # type: ignore[arg-type]
        "body.expanded_config: list, not object",
        "body.expanded_devices: str, not object",
    ]
    assert instance(None, None) == []  # type: ignore[arg-type]
    assert instance({"limits.cpu": None}, {"root": None}) == [
        "body.expanded_config.limits.cpu: not a string",
        "body.expanded_devices.root: a device of type None, which the extract lacks",
    ]


def test_a_request_has_the_parameters_headers_and_body_of_its_call() -> None:
    headers = ["X-Incus-uid", "x-incus-mode", "Content-Type", "X-Incus-write"]
    assert API.request("POST", FILES, ["path", "project"], headers, b"content") == []
    assert API.request("POST", FILES, ["path", "proyect"], ["X-Incus-force"], b"") == [
        "POST /1.0/instances/{name}/files: query parameter proyect is not described",
        "POST /1.0/instances/{name}/files: header X-Incus-force is not described",
    ]
    assert API.request("POST", FILES, [], [], {"content": "x"}) == [
        "POST /1.0/instances/{name}/files: the description has raw body"
    ]
    assert API.request("POST", FILES, [], [], None) == [
        "POST /1.0/instances/{name}/files: the description has raw body"
    ]
    assert API.request("GET", FILES, ["path"], [], b"x") == [
        "GET /1.0/instances/{name}/files: the description has no body"
    ]
    body = {"command": ["true"], "record-output": True, "environment": {"A": "b"}, "user": 0}
    assert API.request("POST", EXEC, ["project"], [], body) == []
    assert API.request("POST", EXEC, [], [], {"comand": ["true"], "record-output": "yes"}) == [
        "POST /1.0/instances/{name}/exec: request.comand: no such key in the description",
        "POST /1.0/instances/{name}/exec: request.record-output: str, not boolean",
    ]
    assert API.request("POST", EXEC, [], [], {"interactive": 0, "user": True}) == [
        "POST /1.0/instances/{name}/exec: request.interactive: int, not boolean",
        "POST /1.0/instances/{name}/exec: request.user: bool, not integer",
    ]
    assert API.request("POST", EXEC, [], [], b'{"command": ["true"]}') == [
        "POST /1.0/instances/{name}/exec: request: bytes, not object"
    ]
    assert API.request("GET", "/1.0/instances", ["project", "all", "project"], [], None) == [
        "GET /1.0/instances: query parameter all is not described",
        "GET /1.0/instances: query parameter project is given twice",
    ]
    falsies: tuple[Any, ...] = ({}, [], 0, False, "")
    for falsy in falsies:
        assert API.request("POST", FILES, [], [], falsy) == [
            "POST /1.0/instances/{name}/files: the description has raw body"
        ]
    assert API.request("GET", FILES, ["path"], [], b"") == []
    assert API.request("POST", EXEC, [], [], None) == [
        "POST /1.0/instances/{name}/exec: no JSON body, and the description has one"
    ]
    # A map the client leaves unset is no difference; Incus reads a null as an empty one.
    assert API.request("POST", EXEC, [], [], {"command": ["true"], "environment": None}) == []


def test_a_response_has_a_status_headers_and_a_body_of_its_call() -> None:
    assert API.response(STATE_CALL, 200, ["Content-Type"], envelope(state())) == []
    assert API.response(STATE_CALL, 201, [], envelope(state())) == [
        f"{STATE_CALL}: status 201 is not described"
    ]
    # A refusal the call does not list is compared with the refusals it lists.
    refusal = {"type": "error", "error": "Instance not found", "error_code": 404}
    assert "404" not in API.calls[STATE_CALL]["responses"]
    assert API.response(STATE_CALL, 404, [], refusal) == []
    assert API.response(STATE_CALL, 404, [], {**refusal, "reason": "gone"}) == [
        f"{STATE_CALL}: 404.reason: no such key in the description"
    ]
    # An answer of Incus has seven keys whatever its kind: each is taken, with its type.
    whole = {"status": "", "status_code": 0, "operation": "", "metadata": None}
    assert API.response(STATE_CALL, 404, [], {**refusal, **whole}) == []
    assert API.response(STATE_CALL, 404, [], {**refusal, **whole, "status_code": "0"}) == [
        f"{STATE_CALL}: 404.status_code: str, not integer"
    ]
    rest = {"operation": "", "error_code": 0, "error": ""}
    assert API.response(STATE_CALL, 200, [], {**envelope(state()), **rest}) == []
    assert API.response(STATE_CALL, 200, [], {**envelope(state()), **rest, "error": None}) == [
        f"{STATE_CALL}: 200.error: NoneType, not string"
    ]
    assert API.response(STATE_CALL, 200, ["X-Incus-type"], envelope(state(usage="1"))) == [
        f"{STATE_CALL}: response header X-Incus-type is not described",
        f"{STATE_CALL}: 200.metadata.disk.root.usage: str, not integer",
    ]
    assert API.response(STATE_CALL, 400, [], {"type": "error", "error": "no", "code": 400}) == [
        f"{STATE_CALL}: 400.code: no such key in the description"
    ]
    assert API.response(STATE_CALL, 200, [], None) == [
        f"{STATE_CALL}: 200: no JSON body, and the description has one"
    ]
    # A record is an object with described properties: never null.
    assert API.response(STATE_CALL, 200, [], envelope(None)) == [
        f"{STATE_CALL}: 200.metadata: NoneType, not object"
    ]
    assert API.response("GET /1.0/instances", 200, [], envelope(None)) == []
    read = "GET /1.0/instances/{name}/files"
    assert API.response(read, 200, ["X-Incus-uid", "x-incus-type", "X-Incus-modified"], None) == []
    assert API.response(read, 200, ["X-Incus-write"], None) == [
        f"{read}: response header X-Incus-write is not described"
    ]
    # The description leaves this answer raw; a directory's names come as JSON all the same,
    # in an answer of Incus like any other.
    assert API.response(read, 200, ["X-Incus-type"], envelope(["a.conf", "b.conf"])) == []
    assert API.response(read, 200, [], {**envelope([]), "names": []}) == [
        f"{read}: 200.names: no such key in an answer of Incus"
    ]
    assert API.response(read, 200, [], ["a.conf"]) == [f"{read}: 200: list, not object"]
    assert API.response(read, 200, [], envelope(None)) == []
    for names in ({"a.conf": 1}, "a.conf", ["a.conf", 7], [["a.conf"]]):
        assert API.response(read, 200, ["X-Incus-type"], envelope(names)) == [
            f"{read}: 200.metadata: not a list of names"
        ]


def test_a_call_is_one_of_the_extract_s_and_a_refusal_needs_a_listed_one() -> None:
    extract = copy.deepcopy(
        {"calls": API.calls, "definitions": API.definitions, "config": API.config}
    )
    one = extract["calls"][STATE_CALL]
    one["responses"] = {"200": one["responses"]["200"]}
    extract["calls"]["GET /1.0/instances/{name}/{part}"] = one
    api = Api(extract)
    # A call that lists no refusal has no form to compare an unlisted one with.
    assert api.response(STATE_CALL, 404, [], {"type": "error"}) == [
        f"{STATE_CALL}: status 404 is not described"
    ]
    # A path that two routes take is no call.
    assert api.call("GET", "/1.0/instances/web/state") is None
    assert api.request("GET", "/1.0/instances/web/state", [], [], None) == [
        "GET /1.0/instances/web/state: no such call in the description"
    ]
    assert api.call("GET", "/1.0/instances/web/files") is None
    assert api.call("GET", "/1.0/instances/web") == "GET /1.0/instances/{name}"


def test_every_refusal_of_the_extract_has_one_form() -> None:
    # A refusal a call does not list is compared with one it lists: they must not differ.
    forms = {
        json.dumps(response, sort_keys=True)
        for call in API.calls.values()
        for status, response in call["responses"].items()
        if int(status) >= FIRST_REFUSAL
    }
    assert len(forms) == 1
    assert json.loads(forms.pop())["schema"]["properties"].keys() == {"error", "error_code", "type"}


def test_every_described_answer_of_the_extract_lists_its_keys_in_place() -> None:
    # An answer's own keys are told from the rest of Incus's seven by its schema's properties.
    schemas = [
        response["schema"]
        for call in API.calls.values()
        for response in call["responses"].values()
        if response["schema"] is not None
    ]
    assert len(schemas) > len(API.calls)
    assert all(schema["type"] == "object" and "properties" in schema for schema in schemas)


def test_a_checked_transport_hands_on_what_was_answered_unchanged() -> None:
    made = [
        httpx.Response(200, json=envelope(state()), headers={"X-Other": "kept"}),
        httpx.Response(200, content=b"\x00raw", headers={"X-Incus-type": "file"}),
        httpx.Response(200, json={"type": "sync", "status": "Success", "status_code": 200}),
    ]
    answers = list(made)
    inner = httpx.MockTransport(lambda request: answers.pop(0))
    with httpx.Client(transport=checked(inner), base_url="https://incus.invalid") as client:
        first = client.get("/1.0/instances/web/state", params={"project": "bench"})
        assert first is made[0]
        assert (first.status_code, first.headers["x-other"], first.json()) == (
            200,
            "kept",
            envelope(state()),
        )
        raw = client.get(FILES, params={"path": "/etc/x"})
        assert raw is made[1]
        assert (raw.content, raw.headers["x-incus-type"]) == (b"\x00raw", "file")
        # A file may be empty: a write without a byte is a write.
        assert client.post(FILES, params={"path": "/etc/x"}, content=b"") is made[2]
        # An encoded character of a name is the character; a content type alone is no body.
        answers.append(made[0])
        typed = {"Content-Type": "application/json"}
        again = client.get("/1.0/instances/we%62/state", params={"project": "x"}, headers=typed)
        assert again is made[0]


@pytest.mark.parametrize(
    ("send", "found"),
    [
        (
            lambda c: c.post(EXEC, content=b'{"command": ["true"]}'),
            "request: bytes, not object",
        ),
        (lambda c: c.post(EXEC), "no JSON body, and the description has one"),
        (
            lambda c: c.post(EXEC, content=b"{", headers={"Content-Type": "application/json"}),
            "a JSON content type, and a body that is no JSON",
        ),
        (
            lambda c: c.post(FILES, json={"content": "x"}),
            "the description has raw body",
        ),
        (
            lambda c: c.get("/1.0/instances?project=a&project=b"),
            "query parameter project is given twice",
        ),
        (lambda c: c.get("/1.0/images"), "GET /1.0/images: no such call in the description"),
        # A path is compared as Incus receives it: an encoded slash is a slash.
        (
            lambda c: c.get("/1.0/instances/a%2Fb/state"),
            "GET /1.0/instances/a/b/state: no such call in the description",
        ),
    ],
)
def test_a_checked_transport_refuses_a_request_before_it_is_answered(send: Any, found: str) -> None:
    asked: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(200, json=envelope(None))

    inner = httpx.MockTransport(answer)
    with httpx.Client(transport=checked(inner), base_url="https://incus.invalid") as client:
        with pytest.raises(AssertionError, match=found):
            send(client)
    assert asked == []


@pytest.mark.parametrize(
    ("answer", "found"),
    [
        (httpx.Response(200, text="<html>"), "200: no JSON body, and the description has one"),
        (
            httpx.Response(200, content=b"", headers={"Content-Type": "application/json"}),
            "200: a JSON content type, and a body that is no JSON",
        ),
        (httpx.Response(200, json=["a", "list"]), "200: list, not object"),
        (httpx.Response(201, json=envelope(state())), "status 201 is not described"),
    ],
)
def test_a_checked_transport_refuses_an_answer_before_the_test_sees_it(
    answer: httpx.Response, found: str
) -> None:
    inner = httpx.MockTransport(lambda request: answer)
    with httpx.Client(transport=checked(inner), base_url="https://incus.invalid") as client:
        with pytest.raises(AssertionError, match=found):
            client.get("/1.0/instances/web/state", params={"project": "bench"})


def test_a_checked_transport_fails_the_test_on_the_first_difference() -> None:
    answers = [envelope(state()), envelope(state()), envelope(state(useage=1))]
    inner = httpx.MockTransport(lambda request: httpx.Response(200, json=answers.pop(0)))
    with httpx.Client(transport=checked(inner), base_url="https://incus.invalid") as client:
        answer = client.get("/1.0/instances/web/state", params={"project": "bench"})
        assert answer.json()["metadata"]["disk"]["root"]["total"] == 4096
        with pytest.raises(AssertionError, match="query parameter proyect is not described"):
            client.get("/1.0/instances/web/state", params={"proyect": "bench"})
        assert len(answers) == 2
        with pytest.raises(AssertionError, match="no such call in the description"):
            client.get("/1.0/images")
        client.get("/1.0/instances/web/state")
        with pytest.raises(AssertionError, match=r"root\.useage: no such key"):
            client.get("/1.0/instances/web/state")
        with pytest.raises(AssertionError, match=r"request\.comand: no such key"):
            client.post(EXEC, json={"comand": ["true"]})
