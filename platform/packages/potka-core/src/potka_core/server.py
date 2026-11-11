"""A service's HTTP server on its handed-over sockets, every request checked.

One uvicorn server serves all of the service's sockets. For each connection
the protocol reads the caller's credentials and which socket accepted it;
before any route, the request is checked against that socket's allowlist
and refused with 403 otherwise; a connection whose caller cannot be
identified is closed. The server binds nothing; it runs on asyncio's loop
with no websocket protocol. On SIGTERM, at any moment once `serve` has
started, it stops accepting, lets requests in flight finish within the
grace period, and the process exits 0. An error in the event loop is logged
as the exception's type and frames, never asyncio's own text, which can
hold a failed task's values.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from types import FrameType
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from uvicorn.protocols.http.h11_impl import H11Protocol

from potka_core.activation import ActivationError, inherited_sockets, service_sockets
from potka_core.logging import setup_logging
from potka_core.peer import Allowlist, Peer, PeerError, allowlist, check_architecture, peer_of
from potka_core.principals import Socket, load_principals

PEER = "potka.peer"
GRACE_SECONDS = 30
log = logging.getLogger("potka.server")
# The ASGI types (as starlette writes them).
Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class PeerProtocol(H11Protocol):
    """uvicorn's HTTP/1.1 protocol, adding the caller and its socket to each request."""

    socket_names: Mapping[Any, str] = {}

    def connection_made(self, transport: asyncio.Transport) -> None:  # type: ignore[override]
        """Read the caller's credentials when a connection is accepted.

        Args:
            transport: The connection.
        """
        super().connection_made(transport)
        sock = transport.get_extra_info("socket")
        try:
            identity = (self.socket_names[sock.getsockname()], peer_of(sock))
        except Exception:  # any failure to identify the caller closes the connection
            log.warning("a connection without known credentials was closed", exc_info=True)
            transport.close()
            return
        app = self.app

        async def with_peer(scope: Scope, receive: Receive, send: Send) -> None:
            extensions = {**(scope.get("extensions") or {}), PEER: identity}
            await app({**scope, "extensions": extensions}, receive, send)

        self.app = with_peer


class Gate:
    """Sends each request to its socket's app if the caller is on that socket's allowlist."""

    def __init__(self, apps: Mapping[str, ASGIApp], allowlists: Mapping[str, Allowlist]) -> None:
        """Gate APPS (by socket name) with ALLOWLISTS (by socket name).

        Args:
            apps: Each socket's app.
            allowlists: Each socket's allowlist.
        """
        self.apps = apps
        self.allowlists = allowlists

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Check and dispatch one request.

        Args:
            scope: The ASGI scope.
            receive: The ASGI receive channel.
            send: The ASGI send channel.
        """
        if scope["type"] == "lifespan":
            asyncio.get_running_loop().set_exception_handler(loop_error)
            while (await receive())["type"] != "lifespan.shutdown":
                await send({"type": "lifespan.startup.complete"})
            await send({"type": "lifespan.shutdown.complete"})
            return
        identity: tuple[str, Peer] | None = (scope.get("extensions") or {}).get(PEER)
        if scope["type"] == "http" and identity is not None:
            name, peer = identity
            if self.allowlists[name].allows(peer):
                await self.apps[name](scope, receive, send)
                return
            log.warning("refused uid %d on %s", peer.uid, self.allowlists[name].socket)
        if scope["type"] == "http":
            await JSONResponse({"detail": "forbidden"}, status_code=403)(scope, receive, send)
        else:
            await send({"type": "websocket.close", "code": 1008})


def loop_error(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
    """Log an error of the event loop: the exception's type and frames only.

    asyncio's own message and context name the failed task, callback or
    future with their ``repr``, which can hold a value; none is written.

    Args:
        loop: The loop.
        context: asyncio's description of the error.
    """
    exc = context.get("exception")
    log.error(
        "an error in the event loop", exc_info=exc if isinstance(exc, BaseException) else None
    )


def health_app(reason: str | None) -> FastAPI:
    """An app with ``/healthz`` and ``/readyz``.

    Args:
        reason: Why the service is not ready, or None when it is.

    Returns:
        The app.
    """
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        if reason is None:
            return JSONResponse({"status": "ready"})
        return JSONResponse({"status": "not ready", "reason": reason}, status_code=503)

    return app


def serve(service: str, app_for: Callable[[Socket], ASGIApp]) -> int:
    """Serve SERVICE's handed-over sockets until SIGTERM.

    Args:
        service: The service's name in the manifest.
        app_for: The app of each of its sockets.

    Returns:
        The exit status: 0 after a stop, 1 when the service cannot start
        (uvicorn ends the process with status 3 if it fails to start itself).
    """
    stopping: list[int] = []

    def stop_before_serving(signum: int, frame: FrameType | None) -> None:
        stopping.append(signum)

    signal.signal(signal.SIGTERM, stop_before_serving)
    try:
        check_architecture()
        principals = load_principals()
        sockets = service_sockets(principals, service, inherited_sockets())
        allowlists = {s.name: allowlist(principals, s) for s in sockets}
    except (ActivationError, PeerError, OSError, ValueError) as exc:
        # The text comes from principals.json, the environment and the
        # sockets, never from a restricted value: it is written on purpose.
        log.error("%s cannot start: %s", service, str(exc))
        return 1
    names = {sock.getsockname(): s.name for s, sock in sockets.items()}
    protocol = type("ServiceProtocol", (PeerProtocol,), {"socket_names": names})
    config = uvicorn.Config(
        Gate({s.name: app_for(s) for s in sockets}, allowlists),
        http=protocol,
        loop="asyncio",
        ws="none",
        lifespan="on",
        log_config=None,
        access_log=False,
        proxy_headers=False,
        server_header=False,
        timeout_graceful_shutdown=GRACE_SECONDS,
    )
    server = uvicorn.Server(config)

    def stop(signum: int, frame: FrameType | None) -> None:
        server.should_exit = True

    # Until uvicorn installs its own handler, and after it re-raises SIGTERM
    # with this handler restored, a SIGTERM asks the server to stop; the
    # process then exits 0.
    signal.signal(signal.SIGTERM, stop)
    if stopping:
        log.info("%s stopped before serving", service)
        return 0
    server.run(sockets=list(sockets.values()))
    log.info("%s stopped", service)
    return 0


def run_not_implemented(service: str) -> int:
    """Serve only ``/healthz`` and ``/readyz`` (503, not implemented) on SERVICE's sockets.

    Args:
        service: The service's name in the manifest.

    Returns:
        The exit status.
    """
    setup_logging(service)
    return serve(service, lambda sock: health_app("not implemented"))
