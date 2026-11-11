"""Who may call a socket: its allowlist and the caller's credentials.

A socket's allowlist comes from its ``clients`` in ``principals.json``: the
UIDs of the client accounts (the file's services), the GIDs of the human
classes' groups, and UID 0 when root is a client. The agent is a client of
the sockets dedicated to it and of no other: its UID is refused on every
other socket, whatever its groups. The caller is known from the connection
itself: ``SO_PEERCRED`` gives its PID, UID and primary GID,
``SO_PEERGROUPS`` its supplementary groups as they were when it connected
(read with libc's ``getsockopt``, which takes a buffer of any size; the
kernel says how large when it is too small). Socket permissions are the
first gate, this check the second; without ACL support infra makes human
sockets 0666 and this check alone separates the clients.
"""

from __future__ import annotations

import ctypes
import errno
import grp
import os
import platform
import socket
import struct
from collections.abc import Callable
from dataclasses import dataclass

from potka_core.principals import AGENT, Principals, Socket

# SO_PEERGROUPS is 59 in Linux's asm-generic socket.h, which x86_64 and
# aarch64 use; other architectures may number it differently.
SO_PEERGROUPS = 59
PEERGROUPS_ARCHITECTURES = frozenset({"x86_64", "aarch64"})
MAX_GROUPS = 65536  # NGROUPS_MAX
ROOT = 0
UCRED = struct.Struct("=iII")  # struct ucred: pid_t, uid_t, gid_t
_LIBC = ctypes.CDLL(None, use_errno=True)
_LIBC.getsockopt.argtypes = [
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint32),
]
_LIBC.getsockopt.restype = ctypes.c_int


class PeerError(RuntimeError):
    """The allowlist cannot be built, or the caller cannot be identified."""


def check_architecture(machine: str | None = None) -> None:
    """Refuse an architecture on which SO_PEERGROUPS is not known to be 59.

    Args:
        machine: The machine type (``platform.machine()`` when None).

    Raises:
        PeerError: The architecture is not x86_64 or aarch64.
    """
    machine = platform.machine() if machine is None else machine
    if machine not in PEERGROUPS_ARCHITECTURES:
        raise PeerError(
            f"unsupported architecture {machine!r}: callers' groups are read with "
            "SO_PEERGROUPS, known only on x86_64 and aarch64"
        )


@dataclass(frozen=True)
class Peer:
    """A caller, as the kernel knows it."""

    pid: int
    uid: int
    gid: int
    groups: frozenset[int]


@dataclass(frozen=True)
class Allowlist:
    """Who may call one socket."""

    socket: str
    uids: frozenset[int]
    gids: frozenset[int]
    refused: frozenset[int] = frozenset()

    def allows(self, peer: Peer) -> bool:
        """Whether PEER may call the socket.

        Args:
            peer: The caller.

        Returns:
            False when its UID is refused (the agent's, on a socket not
            dedicated to it); otherwise True when its UID is a client's, or
            its primary or a supplementary group is a client human class's.
        """
        if peer.uid in self.refused:
            return False
        return peer.uid in self.uids or bool(self.gids & (peer.groups | {peer.gid}))


def _group_gid(name: str) -> int:
    return grp.getgrnam(name).gr_gid


def allowlist(
    principals: Principals, sock: Socket, group_gid: Callable[[str], int] = _group_gid
) -> Allowlist:
    """The allowlist of SOCK.

    Args:
        principals: ``principals.json``.
        sock: One of its sockets.
        group_gid: Resolves a group name to its GID.

    Returns:
        The allowlist.

    Raises:
        PeerError: A client account is not a service of the file, a group
            cannot be resolved, a socket dedicated to the agent names any
            other client, or another socket names the agent.
    """
    by_account = {s.account: s for s in principals.services}
    agents = frozenset(s.uid for s in principals.services if s.profile == AGENT)
    clients = sock.clients
    label = f"{sock.service}/{sock.name}"
    missing = sorted(set(clients.accounts) - set(by_account))
    if missing:
        names = ", ".join(missing)
        raise PeerError(f"{label}: client accounts not services of principals.json: {names}")
    is_agent = [by_account[a].profile == AGENT for a in clients.accounts]
    if sock.agent_dedicated and (clients.humans or clients.root or not all(is_agent)):
        raise PeerError(f"{label}: a socket dedicated to the agent names another client")
    if not sock.agent_dedicated and any(is_agent):
        raise PeerError(f"{label}: a socket not dedicated to the agent names the agent")
    uids = {by_account[a].uid for a in clients.accounts} | ({ROOT} if clients.root else set())
    gids = set()
    for name in clients.human_groups:
        try:
            gids.add(group_gid(name))
        except KeyError:
            raise PeerError(f"{label}: unknown group {name!r}") from None
    refused = frozenset() if sock.agent_dedicated else agents
    return Allowlist(label, frozenset(uids), frozenset(gids), refused)


class BufferTooSmall(Exception):
    """getsockopt's buffer is too small; the kernel says how many bytes it needs."""

    def __init__(self, needed: int) -> None:
        """Record NEEDED, the length the kernel asks for."""
        super().__init__(needed)
        self.needed = needed


def _getsockopt(fd: int, level: int, option: int, length: int) -> bytes:
    """getsockopt(2) with a buffer of LENGTH bytes (Python's own stops at 1024).

    Raises:
        BufferTooSmall: The buffer is too small (ERANGE), with the length needed.
        OSError: Any other error.
    """
    size = ctypes.c_uint32(length)
    buffer = ctypes.create_string_buffer(length)
    if _LIBC.getsockopt(fd, level, option, buffer, ctypes.byref(size)) != 0:
        code = ctypes.get_errno()
        if code == errno.ERANGE:
            raise BufferTooSmall(size.value)
        raise OSError(code, os.strerror(code))
    return buffer.raw[: size.value]


def peer_of(
    conn: socket.socket, getsockopt: Callable[[int, int, int, int], bytes] = _getsockopt
) -> Peer:
    """The caller on the other end of CONN, a connected Unix socket.

    Args:
        conn: The connection.
        getsockopt: Reads a socket option (libc's, by default).

    Returns:
        The caller's PID, UID, primary GID and supplementary groups.

    Raises:
        PeerError: The architecture is not supported, or the kernel's answer
            has an unexpected size.
        OSError: The credentials cannot be read.
    """
    check_architecture()
    fd = conn.fileno()
    raw = getsockopt(fd, socket.SOL_SOCKET, socket.SO_PEERCRED, UCRED.size)
    if len(raw) != UCRED.size:
        raise PeerError(f"SO_PEERCRED gave {len(raw)} bytes, not {UCRED.size}")
    pid, uid, gid = UCRED.unpack(raw)
    length = 4 * 64
    for _ in range(2):
        try:
            raw = getsockopt(fd, socket.SOL_SOCKET, SO_PEERGROUPS, length)
            break
        except BufferTooSmall as small:
            if not length < small.needed <= 4 * MAX_GROUPS:
                raise PeerError(f"SO_PEERGROUPS asked for {small.needed} bytes") from None
            length = small.needed
    else:
        raise PeerError("SO_PEERGROUPS asked for a larger buffer twice")
    if len(raw) % 4:
        raise PeerError(f"SO_PEERGROUPS gave {len(raw)} bytes, not whole groups")
    groups = struct.unpack(f"={len(raw) // 4}I", raw)
    return Peer(pid, uid, gid, frozenset(groups))
