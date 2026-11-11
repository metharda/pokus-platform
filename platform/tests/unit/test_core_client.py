"""Clients over Unix sockets: the socket's path, and no TLS setting from the environment."""

from __future__ import annotations

import asyncio
import socketserver
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest
from potka_core.client import async_client, client, socket_path
from potka_core.principals import load_principals
from potka_core.restricted import Restricted

FIXTURES = Path(__file__).parents[1] / "fixtures"


class Echo(BaseHTTPRequestHandler):
    """Answers with the request's path."""

    def do_GET(self) -> None:
        """Answer."""
        body = self.path.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Write nothing."""

    def address_string(self) -> str:
        """A Unix socket's caller has no address."""
        return "unix"


@pytest.fixture
def served(tmp_path: Path) -> Iterator[str]:
    path = str(tmp_path / "svc.sock")
    server = socketserver.ThreadingUnixStreamServer(path, Echo)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield path
    finally:
        server.shutdown()
        server.server_close()


def test_both_clients_reach_the_socket_and_ignore_ssl_cert_file(
    served: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # httpx reads SSL_CERT_FILE when a transport trusts the environment.
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent/ca.pem")
    with client(served) as c:
        answer = c.get("/healthz")
    assert (answer.status_code, answer.text) == (200, "/healthz")

    async def get() -> str:
        async with async_client(served) as c:
            return (await c.get("/readyz")).text

    assert asyncio.run(get()) == "/readyz"


def test_a_restricted_value_cannot_be_a_header(served: str) -> None:
    with client(served) as c, pytest.raises(TypeError, match=r"^Header value must be str or bytes"):
        c.get("/healthz", headers={"x-run": Restricted("scenario-FIXTURE")})  # type: ignore[arg-type]


def test_the_socket_path_comes_from_principals_json() -> None:
    principals = load_principals(FIXTURES / "principals.json")
    svc = next(s for s in principals.sockets_of("orch") if s.name == "svc")
    assert socket_path(principals, "orch", "svc") == svc.path
    with pytest.raises(KeyError):
        socket_path(principals, "orch", "nope")
