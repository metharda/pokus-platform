"""The Incus client: what it asks, what it reads, what it refuses to send."""

from __future__ import annotations

import inspect
import shutil
import socket
import ssl
import subprocess
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st
from potka_core.endpoints import Incus as Endpoints
from potka_incus import client as incus
from potka_incus.units import MAX_SIZE, UNITS, parse_size
from potka_testing.incus import (
    FINGERPRINT,
    OPERATION,
    PROJECT,
    URL,
    Script,
    ended,
    error,
    started,
    sync,
)

WAIT = f"/1.0/operations/{OPERATION}/wait"
# A run's network, in the form the infrastructure names one.
RUN_NETWORK = "r-0a1b2c3d4e5f"
ROOT_DISK = {"type": "disk", "path": "/", "pool": "p", "size": "8GiB"}
RECORD = {
    "name": "web",
    "status": "Running",
    "status_code": 103,
    "type": "container",
    "profiles": ["target-container"],
    "config": {"volatile.base_image": FINGERPRINT},
    "devices": {},
    "expanded_config": {"volatile.base_image": FINGERPRINT, "security.nesting": "false"},
    "expanded_devices": {"root": {"type": "disk", "path": "/", "pool": "p", "size": "8GiB"}},
}


def record(**changes: Any) -> httpx.Response:
    return sync({**RECORD, **changes})


def test_an_instance_s_record_is_read_in_the_client_s_project() -> None:
    script = Script().on("GET", "/1.0/instances/web", record())
    found = script.client().instance("web")
    assert script.asked() == [("GET", "/1.0/instances/web", {"project": PROJECT})]
    assert (found.name, found.status, found.status_code, found.kind) == (
        "web",
        "Running",
        103,
        "container",
    )
    assert found.profiles == ("target-container",)
    assert found.base_image == FINGERPRINT
    assert found.root_size == 8 * 1024**3
    assert found.expanded_devices["root"]["pool"] == "p"


@pytest.mark.parametrize(
    ("devices", "name", "size"),
    [
        ({}, None, None),
        ({"root": {"type": "disk", "path": "/", "pool": "p"}}, "root", None),
        ({"root": {"type": "disk", "path": "/", "pool": "p", "size": ""}}, "root", None),
        # The root disk is the disk at / that has no source, whatever it is called.
        ({"rootfs": {"type": "disk", "path": "/", "pool": "p", "size": "2GiB"}}, "rootfs", 2**31),
        ({"root": {"type": "disk", "path": "/srv", "pool": "p", "size": "2GiB"}}, None, None),
        ({"root": {"type": "disk", "path": "/", "source": "/mnt", "size": "2GiB"}}, None, None),
        ({"root": {"type": "nic", "network": "r-abc"}}, None, None),
    ],
)
def test_the_root_disk_is_the_disk_at_the_root_and_may_have_no_size(
    devices: dict[str, Any], name: str | None, size: int | None
) -> None:
    script = Script().on(
        "GET", "/1.0/instances/web", sync({"name": "web", "expanded_devices": devices})
    )
    found = script.client().instance("web")
    assert (found.root_device, found.root_size) == (name, size)
    assert (found.base_image, found.profiles, found.config) == (None, (), {})


def test_a_root_disk_s_size_in_another_form_is_refused() -> None:
    devices = {"root": {"type": "disk", "path": "/", "size": "8 gigabytes"}}
    script = Script().on("GET", "/1.0/instances/web", record(expanded_devices=devices))
    with pytest.raises(ValueError, match="not a size as Incus writes one"):
        _ = script.client().instance("web").root_size


def test_a_state_holds_the_use_of_each_disk() -> None:
    state = {"status": "Running", "status_code": 103, "pid": 77}
    script = Script().on(
        "GET",
        "/1.0/instances/web/state",
        sync({**state, "disk": {"root": {"usage": 1024, "total": 4096}}}),
        sync({"status": "Stopped", "status_code": 102}),
    )
    client = script.client()
    assert client.state("web") == incus.State("Running", 103, 77, {"root": incus.Disk(1024, 4096)})
    assert client.state("web") == incus.State("Stopped", 102, 0, {})
    assert script.asked()[0] == ("GET", "/1.0/instances/web/state", {"project": PROJECT})


def test_the_project_s_instances_are_listed_by_name() -> None:
    listed = ["/1.0/instances/web", "/1.0/instances/db?project=bench"]
    script = Script().on("GET", "/1.0/instances", sync(listed), sync([]))
    assert script.client().instances() == ("web", "db")
    assert script.client().instances() == ()


def test_an_instance_is_created_from_a_fingerprint_with_explicit_profiles() -> None:
    script = Script().on("POST", "/1.0/instances", started()).on("GET", WAIT, ended())
    devices = {
        "eth0": {"type": "nic", "network": RUN_NETWORK, "name": "eth0"},
        "root": {"type": "disk", "path": "/", "pool": "p", "size": "8GiB"},
    }
    config = {
        "limits.cpu": "2-3",
        "limits.memory": "1GiB",
        "security.nesting": "true",
        "environment.OTEL_RESOURCE_ATTRIBUTES": "a=b",
    }
    script.client().create(
        "web",
        fingerprint=FINGERPRINT,
        profiles=("target-container",),
        config=config,
        devices=devices,
    )
    assert script.asked() == [
        ("POST", "/1.0/instances", {"project": PROJECT}),
        ("GET", WAIT, {"timeout": str(incus.OPERATION_TIMEOUT)}),
    ]
    assert script.body(0) == {
        "name": "web",
        "type": "container",
        "profiles": ["target-container"],
        "config": config,
        "devices": devices,
        "source": {"type": "image", "fingerprint": FINGERPRINT},
    }


@pytest.mark.parametrize(
    ("arguments", "refusal"),
    [
        ({"fingerprint": "web-nginx-flask/1.0.1"}, ValueError),
        ({"fingerprint": FINGERPRINT[:-1]}, ValueError),
        ({"fingerprint": FINGERPRINT.upper()}, ValueError),
        ({"fingerprint": FINGERPRINT + "\n"}, ValueError),
        ({"fingerprint": 7}, ValueError),
        ({"profiles": ()}, ValueError),
        ({"profiles": ("target-container", 7)}, ValueError),
        ({"profiles": ("",)}, ValueError),
        ({"profiles": "target-container"}, TypeError),
        ({"profiles": (p for p in ["target-container"])}, TypeError),
        ({"kind": "vm"}, ValueError),
        ({"name": "web/../db"}, ValueError),
    ],
)
def test_a_creation_that_is_not_by_fingerprint_and_profiles_sends_nothing(
    arguments: dict[str, Any], refusal: type[Exception]
) -> None:
    script = Script()
    given_: dict[str, Any] = {
        "name": "web",
        "fingerprint": FINGERPRINT,
        "profiles": ("target-container",),
    }
    given_.update(arguments)
    with pytest.raises(refusal, match=r"not a|is needed") as raised:
        script.client().create(given_.pop("name"), **given_)
    assert type(raised.value) is refusal
    assert script.requests == []


def test_a_creation_takes_a_fingerprint_a_profile_list_and_no_other_source() -> None:
    parameters = inspect.signature(incus.Incus.create).parameters
    assert list(parameters) == [
        "self",
        "name",
        "fingerprint",
        "profiles",
        "config",
        "devices",
        "kind",
    ]
    # Nothing takes what is not named: no alias, no source, no other option can be passed.
    kinds = {parameter.kind for parameter in parameters.values()}
    assert kinds == {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}


@pytest.mark.parametrize(
    ("arguments", "refusal", "text"),
    [
        ({"config": {"security.privileged": "true"}}, ValueError, "'security.privileged'"),
        ({"config": {"security.guestapi": "true"}}, ValueError, "'security.guestapi'"),
        ({"config": {"security.nesting.x": "true"}}, ValueError, "'security.nesting.x'"),
        ({"config": {"raw.lxc": "lxc.apparmor.profile=unconfined"}}, ValueError, "'raw.lxc'"),
        ({"config": {"volatile.base_image": FINGERPRINT}}, ValueError, "'volatile.base_image'"),
        ({"config": {"user.note": "x"}}, ValueError, "'user.note'"),
        # Of a target's environment the platform sets one variable, of its limits none of
        # the kernel's own.
        ({"config": {"environment.SCENARIO": "x"}}, ValueError, "'environment.SCENARIO'"),
        (
            {"config": {"environment.OTEL_RESOURCE_ATTRIBUTES.x": "a=b"}},
            ValueError,
            "'environment.OTEL_RESOURCE_ATTRIBUTES.x'",
        ),
        ({"config": {"limits.kernel.memlock": "unlimited"}}, ValueError, "'limits.kernel.memlock'"),
        ({"config": [("limits.cpu", "1")]}, TypeError, "maps strings to strings"),
        ({"config": [("limits.cpu", "1", "x")]}, TypeError, "maps strings to strings"),
        ({"config": "limits.cpu=1"}, TypeError, "maps strings to strings"),
        ({"config": {"limits.cpu": 2}}, TypeError, "maps strings to strings"),
        ({"config": {7: "x"}}, TypeError, "maps strings to strings"),
        (
            {"devices": {"host": {"type": "disk", "source": "/", "path": "/host"}}},
            ValueError,
            "'host'",
        ),
        (
            {"devices": {"data": {"type": "disk", "path": "/srv", "pool": "p"}}},
            ValueError,
            "'data'",
        ),
        ({"devices": {"root": {"type": "disk", "pool": "p"}}}, ValueError, "'root'"),
        # A device replaces the profile's of its name whole: a root disk says its pool and
        # its size, a NIC its network, and that is a run's.
        ({"devices": {"root": {"type": "disk", "path": "/", "pool": "p"}}}, ValueError, "'root'"),
        (
            {"devices": {"root": {"type": "disk", "path": "/", "size": "8GiB"}}},
            ValueError,
            "'root'",
        ),
        (
            {"devices": {"root": {"type": "disk", "path": "/", "pool": "p", "size": "8 GB"}}},
            ValueError,
            "'root'",
        ),
        (
            {"devices": {"root": {"type": "disk", "path": "/", "pool": "p", "size": "0"}}},
            ValueError,
            "'root'",
        ),
        (
            {"devices": {"root": {"type": "disk", "path": "/", "pool": "p", "source": "/srv"}}},
            ValueError,
            "'root'",
        ),
        ({"devices": {"eth0": {"type": "nic", "name": "eth0"}}}, ValueError, "'eth0'"),
        ({"devices": {"eth0": {"type": "nic", "network": "incusbr0"}}}, ValueError, "'eth0'"),
        ({"devices": {"eth0": {"type": "nic", "network": "r-abc"}}}, ValueError, "'eth0'"),
        (
            {"devices": {"eth0": {"type": "nic", "network": RUN_NETWORK + "x"}}},
            ValueError,
            "'eth0'",
        ),
        ({"devices": {"eth0": {"type": "none"}}}, ValueError, "'eth0'"),
        # Each of these has all its device must say, and one thing more or elsewhere.
        (
            {"devices": {"eth0": {"type": "nic", "network": RUN_NETWORK, "nictype": "bridged"}}},
            ValueError,
            "'eth0'",
        ),
        (
            {"devices": {"root": {**ROOT_DISK, "source": "/srv"}}},
            ValueError,
            "'root'",
        ),
        ({"devices": {"data": {**ROOT_DISK, "path": "/srv"}}}, ValueError, "'data'"),
        (
            {"devices": {"otlp": {"type": "proxy", "listen": "tcp:127.0.0.1:4317"}}},
            ValueError,
            "'otlp'",
        ),
        ({"devices": [("eth0", {})]}, TypeError, "map names to maps of strings"),
        ({"devices": {"gpu0": {"type": "gpu"}}}, ValueError, "'gpu0'"),
        ({"devices": {"bare": {}}}, ValueError, "'bare'"),
        (
            {"devices": {"eth0": {"type": "nic", "nictype": "bridged", "parent": "br0"}}},
            ValueError,
            "'eth0'",
        ),
        ({"devices": {"eth0": "nic"}}, TypeError, "map names to maps of strings"),
        ({"devices": {"eth0": {"type": "nic", "mtu": 1500}}}, TypeError, "maps of strings"),
    ],
)
def test_an_instance_is_given_only_what_the_platform_gives_one(
    arguments: dict[str, Any], refusal: type[Exception], text: str
) -> None:
    # An instance's own configuration and devices win over its profiles': what could undo
    # a profile's settings never reaches Incus.
    script = Script()
    with pytest.raises(refusal, match=text) as raised:
        script.client().create(
            "web", fingerprint=FINGERPRINT, profiles=("target-container",), **arguments
        )
    assert type(raised.value) is refusal
    assert script.requests == []


@pytest.mark.parametrize(
    ("answer", "refusal", "text"),
    [
        (
            ended(400, err="no such image", description="Creating instance"),
            incus.OperationFailed,
            "Creating instance: no such image",
        ),
        (ended(401), incus.OperationFailed, "operation: Cancelled"),
        (ended(103), incus.OperationTimeout, "operation: not ended after 120 s"),
        (sync({"id": OPERATION}), incus.IncusError, "operation: an operation without a status"),
    ],
)
def test_an_operation_that_does_not_succeed_is_an_exception(
    answer: httpx.Response, refusal: type[Exception], text: str
) -> None:
    script = Script().on("DELETE", "/1.0/instances/web", started()).on("GET", WAIT, answer)
    with pytest.raises(refusal) as raised:
        script.client().delete("web")
    assert (type(raised.value), str(raised.value)) == (refusal, text)


def test_an_announcement_without_an_operation_is_refused() -> None:
    nameless = httpx.Response(202, json={"type": "async", "metadata": {}})
    script = Script().on("DELETE", "/1.0/instances/web", started("../../instances/db"), nameless)
    for _ in range(2):
        with pytest.raises(incus.IncusError, match="an operation without an identifier"):
            script.client().delete("web")
    assert [path for _, path, _ in script.asked()] == ["/1.0/instances/web"] * 2


def test_an_instance_is_started_stopped_and_deleted() -> None:
    script = Script()
    script.on("PUT", "/1.0/instances/web/state", started(), started(), started())
    script.on("DELETE", "/1.0/instances/web", started())
    script.on("GET", WAIT, ended(), ended(), ended(), ended())
    client = script.client()
    client.start("web")
    client.stop("web")
    client.stop("web", force=True, wait=5)
    client.delete("web")
    bodies = [script.body(index) for index in (0, 2, 4)]
    assert bodies == [
        {"action": "start", "timeout": 120, "force": False},
        {"action": "stop", "timeout": 120, "force": False},
        {"action": "stop", "timeout": 5, "force": True},
    ]
    state = ("PUT", "/1.0/instances/web/state", {"project": PROJECT})
    # A change is waited for as long as Incus is given for it and an answer's time more;
    # the wait names no project, every other request the client's.
    assert script.asked() == [
        state,
        ("GET", WAIT, {"timeout": "150"}),
        state,
        ("GET", WAIT, {"timeout": "150"}),
        state,
        ("GET", WAIT, {"timeout": "35"}),
        ("DELETE", "/1.0/instances/web", {"project": PROJECT}),
        ("GET", WAIT, {"timeout": str(incus.OPERATION_TIMEOUT)}),
    ]


@pytest.mark.parametrize("wait", [-1, True, 2.5, "5", None])
def test_a_wait_is_a_whole_number_of_seconds_and_nothing_else_is_sent(wait: Any) -> None:
    script = Script()
    for change in (script.client().start, script.client().stop):
        with pytest.raises(ValueError, match="not a number of seconds to wait"):
            change("web", wait=wait)
    assert script.requests == []


@pytest.mark.parametrize(
    ("answer", "refusal", "status", "text"),
    [
        (
            error(404, "Instance not found"),
            incus.NotFound,
            404,
            "GET /1.0/instances/web: 404 Instance not found",
        ),
        (
            error(403, "not authorized"),
            incus.Forbidden,
            403,
            "GET /1.0/instances/web: 403 not authorized",
        ),
        (error(500, "boom"), incus.IncusError, 500, "GET /1.0/instances/web: 500 boom"),
        (
            httpx.ConnectError("refused"),
            incus.Unreachable,
            None,
            "GET /1.0/instances/web: ConnectError",
        ),
        (httpx.ReadTimeout("slow"), incus.Unreachable, None, "GET /1.0/instances/web: ReadTimeout"),
    ],
)
def test_a_refusal_and_a_missing_answer_are_exceptions_of_their_kind(
    answer: httpx.Response | Exception,
    refusal: type[incus.IncusError],
    status: int | None,
    text: str,
) -> None:
    script = Script().on("GET", "/1.0/instances/web", answer)
    with pytest.raises(incus.IncusError) as raised:
        script.client().instance("web")
    assert (type(raised.value), raised.value.status, str(raised.value)) == (refusal, status, text)


def test_a_refusal_that_is_not_incus_s_own_is_still_an_exception() -> None:
    # Not an answer Incus gives, so it does not go through the comparison.
    transport = httpx.MockTransport(lambda request: httpx.Response(502, text="<html>"))
    with pytest.raises(incus.IncusError) as raised:
        incus.Incus(URL, PROJECT, transport=transport).instance("web")
    assert (raised.value.status, str(raised.value)) == (502, "GET /1.0/instances/web: 502")
    listed = httpx.MockTransport(lambda request: httpx.Response(404, json=["no", "object"]))
    with pytest.raises(incus.NotFound) as missing:
        incus.Incus(URL, PROJECT, transport=listed).instance("web")
    assert str(missing.value) == "GET /1.0/instances/web: 404"


@pytest.mark.parametrize(
    "content",
    [
        {"text": "<html>"},
        {"json": ["a", "list"]},
        {"json": {"metadata": "text"}},
        {"json": {"metadata": 7}},
    ],
)
def test_a_success_that_is_not_in_incus_s_form_is_an_exception(content: dict[str, Any]) -> None:
    # Not answers Incus gives, so they do not go through the comparison.
    transport = httpx.MockTransport(lambda request: httpx.Response(200, **content))
    client = incus.Incus(URL, PROJECT, transport=transport)
    for call in (client.instances, lambda: client.instance("web"), lambda: client.state("web")):
        with pytest.raises(incus.IncusError) as raised:
            call()
        assert (type(raised.value), raised.value.status, str(raised.value)) == (
            incus.IncusError,
            200,
            "an answer that is not in Incus's form",
        )


def test_metadata_of_another_kind_than_the_call_s_is_not_read() -> None:
    assert answering([]).instances() == ()
    with pytest.raises(incus.IncusError, match="not in Incus's form"):
        answering([]).instance("web")
    with pytest.raises(incus.IncusError, match="not in Incus's form"):
        answering({}).instances()
    with pytest.raises(incus.IncusError, match="not in Incus's form"):
        answering(["x"]).state("web")


def test_a_list_or_a_map_that_incus_sends_as_null_is_empty_and_a_record_is_never_null() -> None:
    script = Script().on("GET", "/1.0/instances", sync(None))
    nothing = {"profiles": None, "config": None, "devices": None, "expanded_devices": None}
    script.on("GET", "/1.0/instances/web", sync({"name": "web", "status": "Stopped", **nothing}))
    stopped = {"status": "Stopped", "status_code": 102, "pid": 0}
    script.on("GET", "/1.0/instances/web/state", sync({**stopped, "disk": None, "network": None}))
    client = script.client()
    assert client.instances() == ()
    found = client.instance("web")
    assert (found.profiles, found.config, found.devices, found.root_device) == ((), {}, {}, None)
    assert client.state("web").disk == {}
    # A record that is null is no answer of Incus: read through a transport of its own,
    # because the comparison with Incus's description refuses it first.
    null = httpx.MockTransport(lambda request: httpx.Response(200, json={"metadata": None}))
    bare = incus.Incus(URL, PROJECT, transport=null)
    for call in (lambda: bare.instance("web"), lambda: bare.state("web")):
        with pytest.raises(incus.IncusError, match="not in Incus's form"):
            call()
    assert bare.instances() == ()
    # An answer without the key at all is none of Incus's, for a list too.
    keyless = httpx.MockTransport(lambda request: httpx.Response(200, json={"type": "sync"}))
    with pytest.raises(incus.IncusError, match="not in Incus's form"):
        incus.Incus(URL, PROJECT, transport=keyless).instances()


def answering(*metadata: Any) -> incus.Incus:
    """A client whose answers hold METADATA in turn, without the comparison."""
    left = list(metadata)

    def answer(request: httpx.Request) -> httpx.Response:
        body = {"type": "sync", "status": "Success", "status_code": 200, "metadata": left.pop(0)}
        return httpx.Response(200, json=body)

    return incus.Incus(URL, PROJECT, transport=httpx.MockTransport(answer))


@pytest.mark.parametrize(
    "record",
    [
        {"profiles": "target-container"},
        {"profiles": ["target-container", 7]},
        {"status_code": "103"},
        {"status_code": True},
        {"name": 7},
        {"config": ["a"]},
        {"config": {"limits.cpu": 2}},
        {"devices": {"root": "disk"}},
        {"expanded_devices": {"root": {"type": "disk", "path": "/", "size": 8}}},
        {"expanded_devices": ["root"]},
    ],
)
def test_an_instance_s_record_with_a_field_of_another_type_is_not_read(
    record: dict[str, Any],
) -> None:
    with pytest.raises(incus.IncusError) as raised:
        answering({"name": "web", **record}).instance("web")
    assert (type(raised.value), str(raised.value)) == (incus.IncusError, incus.FORM)


@pytest.mark.parametrize(
    "state",
    [
        {"disk": ["root"]},
        {"disk": {"root": None}},
        {"disk": {"root": {"usage": "1024"}}},
        {"disk": {"root": {"usage": 1.9}}},
        {"disk": {"root": {"usage": 1, "total": True}}},
        {"pid": "77"},
        {"status": 103},
    ],
)
def test_a_state_with_a_field_of_another_type_is_not_read(state: dict[str, Any]) -> None:
    with pytest.raises(incus.IncusError) as raised:
        answering(state).state("web")
    assert (type(raised.value), str(raised.value)) == (incus.IncusError, incus.FORM)


@pytest.mark.parametrize(
    "listed",
    [
        [7],
        [None],
        ["/1.0/instances/"],
        ["/1.0/instances/web one"],
        [["/1.0/instances/web"]],
        # An address of another place, or of another project, is no instance of this one.
        ["/1.0/other/web"],
        ["web"],
        ["/1.0/instances/web?project=build"],
        ["/1.0/instances/web?project=bench&all=1"],
        ["/1.0/instances/web#x"],
    ],
)
def test_a_listed_instance_that_is_no_name_is_not_read(listed: list[Any]) -> None:
    with pytest.raises(incus.IncusError, match="not in Incus's form"):
        answering(listed).instances()


@pytest.mark.parametrize(
    ("code", "refusal", "text"),
    [
        ("200", incus.IncusError, "operation: an operation without a status"),
        (200.0, incus.IncusError, "operation: an operation without a status"),
        (True, incus.IncusError, "operation: an operation without a status"),
        (None, incus.IncusError, "operation: an operation without a status"),
        (112, incus.OperationTimeout, "operation: not ended after 120 s"),
    ],
)
def test_only_the_number_200_is_an_operation_s_success(
    code: Any, refusal: type[Exception], text: str
) -> None:
    client = answering({"id": OPERATION, "status_code": code})
    with pytest.raises(refusal) as raised:
        client._done(started())
    assert (type(raised.value), str(raised.value)) == (refusal, text)


def test_an_operation_s_record_goes_with_its_exception() -> None:
    failed = {"id": OPERATION, "status_code": 400, "err": "no space", "description": "Creating"}
    with pytest.raises(incus.OperationFailed, match="Creating: no space") as raised:
        answering(failed)._done(started())
    assert raised.value.operation == failed
    running = {"id": OPERATION, "status_code": 103, "status": "Running"}
    with pytest.raises(incus.OperationTimeout) as late:
        answering(running)._done(started())
    assert late.value.operation["id"] == OPERATION
    # The record of another operation than the one waited for is no answer to the wait.
    other = {"id": OPERATION[:-1] + "8", "status_code": 200}
    with pytest.raises(incus.IncusError, match="not the operation that was waited for"):
        answering(other)._done(started())


def test_nothing_a_call_passes_can_name_another_project() -> None:
    script = Script().on("GET", "/1.0/instances", sync([]))
    script.client()._call("GET", "/1.0/instances", params={"project": "build", "filter": "x"})
    sent = script.requests[0].url.params.multi_items()
    assert sorted(sent) == [("filter", "x"), ("project", PROJECT)]


@pytest.mark.parametrize(
    "name",
    ["", "-web", "web-", "123", "a" * 64, "we b", "web/state", "..", "web?x=1", "wéb", "web\n", 7],
)
def test_what_is_not_an_instance_name_reaches_no_address(name: Any) -> None:
    script = Script()
    client = script.client()
    for call in (client.instance, client.state, client.start, client.stop, client.delete):
        with pytest.raises(ValueError, match="not an instance name"):
            call(name)
    assert script.requests == []
    assert [incus.checked_name(n) for n in ("a", "web-1", "W3b", "a" * 63, "1a")] == [
        "a",
        "web-1",
        "W3b",
        "a" * 63,
        "1a",
    ]


def test_a_client_has_a_certificate_or_a_transport_and_a_project_of_the_platform(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transport = httpx.MockTransport(Script())
    with pytest.raises(ValueError, match="a certificate or a transport"):
        incus.Incus(URL, PROJECT)
    with pytest.raises(ValueError, match="a certificate or a transport"):
        incus.Incus(URL, PROJECT, certificate=incus.Certificate.of(tmp_path), transport=transport)
    # Over plain HTTP no certificate would be shown or checked: refused before any file is read.
    for plain in ("http://incus.invalid", "incus.invalid:8443", "HTTP://incus.invalid", ""):
        with pytest.raises(ValueError, match="Incus is reached over https"):
            incus.Incus(plain, PROJECT, certificate=incus.Certificate.of(tmp_path))
    endpoints = Endpoints(
        url=URL,
        cert_dir=str(tmp_path),
        projects=("bench", "build"),
        run_network_socket="/run/x.sock",
    )
    with pytest.raises(ValueError, match="not a project of the platform: 'default'"):
        incus.Incus.for_service(endpoints, "potka-exec", "default")
    # An account's name is the name of a directory below the certificates', and no path.
    for account in ("../potka-exec", "/etc/ssl", "potka/exec", "", "Potka", "potka exec", 7):
        with pytest.raises(ValueError, match="not an account name"):
            incus.Incus.for_service(endpoints, account, "bench")  # type: ignore[arg-type]
    used: list[incus.Certificate] = []

    def context(self: incus.Certificate) -> ssl.SSLContext:
        used.append(self)
        return ssl.create_default_context()

    monkeypatch.setattr(incus.Certificate, "context", context)
    with incus.Incus.for_service(endpoints, "potka-exec", "bench") as client:
        assert client.project == "bench"
    directory = tmp_path / "potka-exec"
    assert used == [
        incus.Certificate(
            directory / "client.crt", directory / "client.key", directory / "server.crt"
        )
    ]


def certificate(directory: Path) -> incus.Certificate:
    """A self-signed certificate and its key, used as a service's and as the server's."""
    directory.mkdir()
    made = incus.Certificate.of(directory)
    arguments = ["req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1"]
    arguments += ["-nodes", "-days", "1", "-subj", "/CN=incus"]
    arguments += ["-keyout", str(made.key), "-out", str(made.client)]
    openssl = shutil.which("openssl")
    assert openssl is not None
    subprocess.run([openssl, *arguments], check=True, capture_output=True)  # noqa: S603 (fixed)
    made.server.write_bytes(made.client.read_bytes())
    return made


def signed(directory: Path, by: incus.Certificate) -> incus.Certificate:
    """A certificate and its key, signed by the certificate BY (which is one that may sign)."""
    directory.mkdir()
    made = incus.Certificate.of(directory)
    openssl = shutil.which("openssl")
    assert openssl is not None
    request = directory / "request.csr"
    asked = ["req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes"]
    asked += ["-subj", "/CN=signed", "-keyout", str(made.key), "-out", str(request)]
    subprocess.run([openssl, *asked], check=True, capture_output=True)  # noqa: S603 (fixed)
    issue = ["x509", "-req", "-in", str(request), "-CA", str(by.client), "-CAkey", str(by.key)]
    issue += ["-CAcreateserial", "-days", "1", "-out", str(made.client)]
    subprocess.run([openssl, *issue], check=True, capture_output=True)  # noqa: S603 (fixed)
    return made


def greeted(shown: incus.Certificate, trusting: ssl.SSLContext, name: str) -> bytes:
    """What a client that trusts as TRUSTING reads from a server that shows SHOWN."""
    serving = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    serving.load_cert_chain(shown.client, shown.key)
    with socket.create_server(("127.0.0.1", 0)) as listener:

        def serve() -> None:
            connection, _ = listener.accept()
            try:
                with serving.wrap_socket(connection, server_side=True) as secured:
                    secured.sendall(b"ok")
            except (ssl.SSLError, OSError):
                connection.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            with socket.create_connection(("127.0.0.1", listener.getsockname()[1])) as raw:
                with trusting.wrap_socket(raw, server_hostname=name) as secured:
                    return secured.recv(2)
        finally:
            thread.join(5)


def test_only_the_pinned_server_certificate_is_trusted_whatever_its_name(tmp_path: Path) -> None:
    ours, other = certificate(tmp_path / "ours"), certificate(tmp_path / "other")
    context = ours.context()
    assert (context.verify_mode, context.check_hostname) == (ssl.CERT_REQUIRED, False)
    # The pinned certificate, under a name that is not its own.
    assert greeted(ours, context, "not-its-name.invalid") == b"ok"
    # Another certificate, even under the pinned one's name.
    with pytest.raises(ssl.SSLCertVerificationError):
        greeted(ours, other.context(), "incus")
    # A certificate the pinned one signed is taken, one that another signed is not: the
    # pinned file is the only trust anchor, and no store of the machine is read.
    assert greeted(signed(tmp_path / "by-ours", ours), context, "signed.invalid") == b"ok"
    with pytest.raises(ssl.SSLCertVerificationError):
        greeted(signed(tmp_path / "by-other", other), context, "signed.invalid")
    assert context.cert_store_stats()["x509_ca"] <= 1


@pytest.mark.parametrize(
    ("text", "size"),
    [
        ("0", 0),
        ("1024", 1024),
        ("7B", 7),
        ("7 bytes", 7),
        ("5kB", 5000),
        ("8GiB", 8 * 1024**3),
        ("2EB", 2 * 1000**6),
    ],
)
def test_a_size_is_a_whole_number_and_one_of_incus_s_units(text: str, size: int) -> None:
    assert parse_size(text) == size


# Every unit Incus writes a size in, each with its own factor: decimal ones in steps of
# 1000, binary ones in steps of 1024, and the three ways to write plain bytes.
FACTORS = {
    "": 1,
    "B": 1,
    " bytes": 1,
    "kB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
    "PB": 1000**5,
    "EB": 1000**6,
    "KiB": 1024,
    "MiB": 1024**2,
    "GiB": 1024**3,
    "TiB": 1024**4,
    "PiB": 1024**5,
    "EiB": 1024**6,
}


def test_the_units_are_incus_s_fifteen_each_with_its_factor() -> None:
    assert UNITS == FACTORS


@given(st.integers(min_value=0, max_value=2**63 - 1), st.sampled_from(sorted(FACTORS)))
def test_every_unit_multiplies_its_number_up_to_what_incus_holds(number: int, unit: str) -> None:
    size = number * FACTORS[unit]
    for text in (f"{number}{unit}", f"00{number}{unit}"):
        if size <= MAX_SIZE:
            assert parse_size(text) == size
        else:
            with pytest.raises(ValueError, match="not a size as Incus writes one"):
                parse_size(text)


def test_the_largest_size_is_the_largest_number_incus_holds() -> None:
    assert MAX_SIZE == 2**63 - 1
    assert parse_size("9223372036854775807") == MAX_SIZE
    assert parse_size("7EiB") == 7 * 1024**6
    for text in ("9223372036854775808", "8EiB", "9223372036854775807kB"):
        with pytest.raises(ValueError, match="not a size as Incus writes one"):
            parse_size(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "GiB",
        "8 GiB",
        "8gib",
        "8Gi",
        "8G",
        "8KB",
        "8kiB",
        "8mB",
        "1.5GiB",
        "-1",
        "+8",
        "8GiB\n",
        " 8",
        "8GiB 1",
        "8  bytes",
        "٨",
    ],
)
def test_any_other_text_is_no_size(text: str) -> None:
    with pytest.raises(ValueError, match="not a size as Incus writes one"):
        parse_size(text)
