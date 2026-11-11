"""The gate before every route, and the health app."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import threading
import time
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from potka_core import server
from potka_core.logging import JSONFormatter
from potka_core.peer import Allowlist, Peer
from potka_core.principals import load_principals
from potka_core.server import PEER, Gate, PeerProtocol, health_app, serve

FIXTURES = Path(__file__).parents[1] / "fixtures"
TRUTH = "scenario-FIXTURE-disk-full"
WATCHDOG = 10.0

ALLOWED = Peer(1, 61001, 61001, frozenset())
STRANGER = Peer(1, 6000, 6000, frozenset())


async def call(app: Any, scope: dict[str, Any]) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []
    messages = [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive() -> dict[str, Any]:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


def http(path: str, identity: tuple[str, Peer] | None) -> dict[str, Any]:
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "http_version": "1.1",
        "scheme": "http",
        "server": None,
        "client": None,
        "extensions": {},
    }
    if identity is not None:
        scope["extensions"] = {PEER: identity}
    return scope


@pytest.fixture
def gate() -> Gate:
    apps = {"api": health_app("not implemented"), "svc": health_app(None)}
    allowlists = {
        "api": Allowlist("orch/api", frozenset({61001}), frozenset()),
        "svc": Allowlist("orch/svc", frozenset({61002}), frozenset()),
    }
    return Gate(apps, allowlists)


@pytest.mark.parametrize("path", ["/healthz", "/readyz", "/no-such-route"])
def test_a_caller_outside_the_socket_s_allowlist_gets_403_everywhere(gate: Gate, path: str) -> None:
    for identity in [("api", STRANGER), ("svc", ALLOWED), None]:
        sent = asyncio.run(call(gate, http(path, identity)))
        assert sent[0]["status"] == 403
        assert sent[1]["body"] == b'{"detail":"forbidden"}'


def test_an_allowed_caller_reaches_the_routes(gate: Gate) -> None:
    assert asyncio.run(call(gate, http("/healthz", ("api", ALLOWED))))[0]["status"] == 200
    assert asyncio.run(call(gate, http("/readyz", ("api", ALLOWED))))[0]["status"] == 503
    assert asyncio.run(call(gate, http("/no-such-route", ("api", ALLOWED))))[0]["status"] == 404


def test_a_request_reaches_the_app_of_the_socket_it_arrived_on(gate: Gate) -> None:
    svc_caller = Peer(1, 61002, 61002, frozenset())
    assert asyncio.run(call(gate, http("/readyz", ("svc", svc_caller))))[0]["status"] == 200
    assert asyncio.run(call(gate, http("/readyz", ("api", ALLOWED))))[0]["status"] == 503


def test_an_event_loop_error_is_logged_without_asyncio_s_text(
    gate: Gate, caplog: pytest.LogCaptureFixture
) -> None:
    async def start_then_fail() -> None:
        steps = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]

        async def receive() -> dict[str, Any]:
            return steps.pop(0)

        async def send(message: MutableMapping[str, Any]) -> None:
            pass

        await gate({"type": "lifespan"}, receive, send)
        asyncio.get_running_loop().call_exception_handler(
            {
                "message": f"Task exception was never retrieved: {TRUTH}",
                "exception": KeyError(TRUTH),
            }
        )

    with caplog.at_level(logging.ERROR):
        asyncio.run(start_then_fail())
    lines = [JSONFormatter("orch").format(record) for record in caplog.records]
    assert [json.loads(line)["message"] for line in lines] == ["an error in the event loop"]
    assert "KeyError (message not written)" in lines[0]
    assert TRUTH not in lines[0]


def test_a_websocket_is_closed(gate: Gate) -> None:
    sent = asyncio.run(call(gate, {**http("/", ("api", ALLOWED)), "type": "websocket"}))
    assert sent == [{"type": "websocket.close", "code": 1008}]


def answer(app: Any, path: str) -> tuple[int, Any]:
    sent = asyncio.run(call(app, http(path, None)))
    return sent[0]["status"], json.loads(sent[1]["body"])


def test_the_health_app() -> None:
    assert answer(health_app("not implemented"), "/healthz") == (200, {"status": "ok"})
    assert answer(health_app("not implemented"), "/readyz") == (
        503,
        {"status": "not ready", "reason": "not implemented"},
    )
    assert answer(health_app(None), "/readyz") == (200, {"status": "ready"})


class Transport:
    """A connection's transport, enough for the protocol."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.closed = False

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        return {"socket": self.sock}.get(name, default)

    def close(self) -> None:
        self.closed = True

    def is_closing(self) -> bool:
        return self.closed


def protocol(names: dict[Any, str], app: Any) -> PeerProtocol:
    config = uvicorn.Config(app, loop="asyncio", ws="none", log_config=None)
    kind = type("TestProtocol", (PeerProtocol,), {"socket_names": names})
    proto: PeerProtocol = kind(
        config, uvicorn.server.ServerState(), {}, _loop=asyncio.new_event_loop()
    )
    return proto


def test_the_protocol_puts_the_caller_and_its_socket_in_each_request() -> None:
    seen: list[Any] = []

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        seen.append(scope["extensions"][PEER])

    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with a, b:
        proto = protocol({a.getsockname(): "api"}, app)
        transport = Transport(a)
        proto.connection_made(transport)  # type: ignore[arg-type]
        asyncio.run(proto.app({"type": "http"}, None, None))
        proto.loop.close()
    assert not transport.closed
    assert seen == [("api", Peer(os.getpid(), os.getuid(), os.getgid(), frozenset(os.getgroups())))]


@pytest.mark.parametrize("failure", ["unknown socket", "credentials"])
def test_a_connection_without_known_credentials_is_closed(
    failure: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(sock: socket.socket) -> Peer:
        raise ValueError("unexpected")

    if failure == "credentials":
        monkeypatch.setattr(server, "peer_of", broken)
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with a, b:
        names = {} if failure == "unknown socket" else {a.getsockname(): "api"}
        proto = protocol(names, health_app(None))
        transport = Transport(a)
        with caplog.at_level(logging.WARNING):
            proto.connection_made(transport)  # type: ignore[arg-type]
        proto.loop.close()
    assert transport.closed
    (record,) = [r for r in caplog.records if r.name == "potka.server"]
    assert record.exc_info is not None
    assert record.exc_info[0] is (KeyError if failure == "unknown socket" else ValueError)
    line = json.loads(JSONFormatter("orch").format(record))
    assert line["message"] == "a connection without known credentials was closed"
    kind = "KeyError" if failure == "unknown socket" else "ValueError"
    assert line["exception"].endswith(f"{kind} (message not written)")
    assert "unexpected" not in line["exception"]


@pytest.fixture
def sigterm() -> Iterator[None]:
    """Restore the test process's SIGTERM handler after serve() changed it.

    A watchdog sends another SIGTERM after WATCHDOG seconds, so a server
    that missed the first one ends and the test fails on its time instead
    of hanging.
    """
    before = signal.getsignal(signal.SIGTERM)
    watchdog = threading.Timer(WATCHDOG, os.kill, (os.getpid(), signal.SIGTERM))
    watchdog.start()
    try:
        yield
    finally:
        watchdog.cancel()
        signal.signal(signal.SIGTERM, before)


@pytest.fixture
def svc_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[socket.socket]:
    """serve("orch") with only orch/svc handed over, listening in tmp_path."""
    principals = load_principals(FIXTURES / "principals.json")
    svc = next(s for s in principals.sockets_of("orch") if s.name == "svc")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(tmp_path / "svc.sock"))
    listener.listen()
    monkeypatch.setattr(server, "load_principals", lambda: principals)
    monkeypatch.setattr(server, "inherited_sockets", dict)
    monkeypatch.setattr(server, "service_sockets", lambda p, s, i: {svc: listener})
    try:
        yield listener
    finally:
        listener.close()


def test_a_sigterm_before_serving_stops_the_service(
    svc_only: socket.socket,
    sigterm: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def load_then_sigterm() -> Any:
        os.kill(os.getpid(), signal.SIGTERM)
        return load_principals(FIXTURES / "principals.json")

    monkeypatch.setattr(server, "load_principals", load_then_sigterm)
    began = time.monotonic()
    with caplog.at_level(logging.INFO):
        assert serve("orch", lambda sock: health_app(None)) == 0
    assert time.monotonic() - began < WATCHDOG / 2
    assert "orch stopped before serving" in caplog.text


def test_a_sigterm_before_uvicorn_s_own_handler_stops_the_server(
    svc_only: socket.socket,
    sigterm: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    factory = uvicorn.Config.get_loop_factory

    def sigterm_then_factory(config: uvicorn.Config) -> Any:
        os.kill(os.getpid(), signal.SIGTERM)
        return factory(config)

    monkeypatch.setattr(uvicorn.Config, "get_loop_factory", sigterm_then_factory)
    began = time.monotonic()
    with caplog.at_level(logging.INFO):
        assert serve("orch", lambda sock: health_app(None)) == 0
    assert time.monotonic() - began < WATCHDOG / 2
    assert "orch stopped" in caplog.text
    assert "before serving" not in caplog.text


def test_the_server_runs_on_asyncio_without_websockets(
    svc_only: socket.socket, sigterm: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    configs: list[uvicorn.Config] = []
    monkeypatch.setattr(uvicorn.Server, "run", lambda self, sockets: configs.append(self.config))
    assert serve("orch", lambda sock: health_app(None)) == 0
    assert [(c.loop, c.ws, c.lifespan, c.proxy_headers) for c in configs] == [
        ("asyncio", "none", "on", False)
    ]


def test_an_unsupported_architecture_stops_the_service_before_its_sockets(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, sigterm: None
) -> None:
    monkeypatch.setattr("platform.machine", lambda: "s390x")
    monkeypatch.setenv("LISTEN_PID", "1")
    with caplog.at_level(logging.ERROR):
        assert serve("gw", lambda sock: health_app(None)) == 1
    assert "unsupported architecture 's390x'" in caplog.text
    assert "LISTEN_PID" in os.environ
