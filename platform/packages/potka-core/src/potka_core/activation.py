"""The sockets systemd hands a service (socket activation).

systemd passes them as descriptors from 3 on, with ``LISTEN_PID`` (the
process they are for), ``LISTEN_FDS`` (how many) and ``LISTEN_FDNAMES``
(their names, the ``fd_name`` of each socket in ``principals.json``). A
service never creates, binds or removes a socket. Each handed-over socket
must listen on the path ``principals.json`` gives it, so the socket file's
permissions (the first gate) and the allowlist (the second) are the same
socket's.
"""

from __future__ import annotations

import os
import re
import socket
from collections.abc import Mapping, MutableMapping

from potka_core.principals import Principals, Socket

FIRST_FD = 3
VARIABLES = ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES")


class ActivationError(RuntimeError):
    """The sockets systemd should have handed over are missing or wrong."""


def inherited_sockets(
    environ: MutableMapping[str, str] | None = None, first_fd: int = FIRST_FD
) -> dict[str, socket.socket]:
    """The listening Unix sockets handed to this process, by name.

    The ``LISTEN_*`` variables are removed, so no child process takes them.

    Args:
        environ: The environment (the process's own when None).
        first_fd: The first descriptor (3, as systemd passes them).

    Returns:
        Each socket by its name in ``LISTEN_FDNAMES``.

    Raises:
        ActivationError: The variables are missing, are for another process,
            disagree, or a descriptor is not a listening Unix stream socket.
    """
    env = os.environ if environ is None else environ
    values = {name: env.pop(name, None) for name in VARIABLES}
    if values["LISTEN_PID"] != str(os.getpid()):
        raise ActivationError("no sockets from systemd for this process (LISTEN_PID)")
    names = (values["LISTEN_FDNAMES"] or "").split(":")
    count = values["LISTEN_FDS"] or ""
    if not re.fullmatch(r"[0-9]+", count) or int(count) != len(names):
        raise ActivationError("LISTEN_FDS and LISTEN_FDNAMES disagree")
    if len(set(names)) != len(names):
        raise ActivationError("a socket name appears twice in LISTEN_FDNAMES")
    sockets = {}
    for offset, name in enumerate(names):
        try:
            sock = socket.socket(fileno=first_fd + offset)
        except OSError:
            raise ActivationError(
                f"descriptor {first_fd + offset} ({name}) is not a socket"
            ) from None
        listening = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
        if sock.family != socket.AF_UNIX or sock.type != socket.SOCK_STREAM or not listening:
            raise ActivationError(f"the socket {name} is not a listening Unix stream socket")
        sock.set_inheritable(False)
        sockets[name] = sock
    return sockets


def service_sockets(
    principals: Principals, service: str, inherited: Mapping[str, socket.socket]
) -> dict[Socket, socket.socket]:
    """Every socket ``principals.json`` lists for SERVICE, with its descriptor.

    Args:
        principals: ``principals.json``.
        service: The service's name.
        inherited: The handed-over sockets, by name.

    Returns:
        Each of the service's sockets with its descriptor.

    Raises:
        ActivationError: A socket of the service was not handed over, one
            was handed over that the service does not serve, or one does not
            listen on its path in ``principals.json``.
    """
    expected = {s.fd_name: s for s in principals.sockets_of(service)}
    missing, extra = sorted(set(expected) - set(inherited)), sorted(set(inherited) - set(expected))
    if missing or extra:
        raise ActivationError(f"sockets missing: {missing}, not the service's: {extra}")
    for name, sock in inherited.items():
        address = sock.getsockname()
        if address != expected[name].path:
            raise ActivationError(
                f"the socket {name} listens on {address!r}, not on {expected[name].path}"
            )
    return {expected[name]: sock for name, sock in inherited.items()}
