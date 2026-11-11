"""HTTP clients over Unix sockets: the only way services call each other.

A client takes a socket path (from ``principals.json``), never a host or a
port, and reads nothing from the environment (no proxy, no TLS setting).
httpx refuses a header value that is not a string or bytes, so a
restricted value cannot become a header.
"""

from __future__ import annotations

import httpx

from potka_core.principals import Principals

BASE_URL = "http://potka"
TIMEOUT = 30.0


def socket_path(principals: Principals, service: str, name: str) -> str:
    """The path of SERVICE's socket NAME.

    Args:
        principals: ``principals.json``.
        service: The serving service.
        name: The socket's name in the manifest.

    Returns:
        The socket's path.

    Raises:
        KeyError: No such socket.
    """
    for sock in principals.sockets_of(service):
        if sock.name == name:
            return sock.path
    raise KeyError(f"no socket {service}/{name} in principals.json")


def client(path: str, timeout: float = TIMEOUT) -> httpx.Client:
    """A client of the service listening on the Unix socket PATH.

    Args:
        path: The socket's path.
        timeout: Seconds per request.

    Returns:
        The client; URLs are paths below ``http://potka``.
    """
    return httpx.Client(
        transport=httpx.HTTPTransport(uds=path, trust_env=False),
        base_url=BASE_URL,
        timeout=timeout,
        trust_env=False,
    )


def async_client(path: str, timeout: float = TIMEOUT) -> httpx.AsyncClient:
    """An asynchronous client of the service listening on the Unix socket PATH.

    Args:
        path: The socket's path.
        timeout: Seconds per request.

    Returns:
        The client; URLs are paths below ``http://potka``.
    """
    return httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(uds=path, trust_env=False),
        base_url=BASE_URL,
        timeout=timeout,
        trust_env=False,
    )
