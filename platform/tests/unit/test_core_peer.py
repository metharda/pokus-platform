"""Socket allowlists from principals.json, and the caller's credentials."""

from __future__ import annotations

import errno
import grp
import os
import socket
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from potka_core.peer import (
    MAX_GROUPS,
    SO_PEERGROUPS,
    UCRED,
    BufferTooSmall,
    Peer,
    PeerError,
    _getsockopt,
    _group_gid,
    allowlist,
    check_architecture,
    peer_of,
)
from potka_core.principals import Job, Principals, load_principals

FIXTURES = Path(__file__).parents[1] / "fixtures"
GROUPS = {"pinka": 7001, "pinka-admin": 7002}
# The sockets' own groups: no caller is admitted for being in one of them.
GROUPS |= {"potka-orch-api": 7100, "potka-orch-svc": 7101, "potka-gw-svc": 7102}
UIDS = {"orch": 61001, "agent": 61002, "gw": 61003, "exec": 61004, "inject": 61005}
UIDS |= {"verify": 61006, "eval": 61007}
# Every service's primary group differs from its UID, so a UID/GID mix-up shows.
CALLERS = {name: Peer(1, uid, uid + 1000, frozenset()) for name, uid in UIDS.items()}
CALLERS |= {
    "root": Peer(1, 0, 0, frozenset()),
    "researcher": Peer(1, 5001, 5001, frozenset({100, 7001})),
    "researcher-primary": Peer(1, 5002, 7001, frozenset()),
    "admin": Peer(1, 5003, 5003, frozenset({7002})),
    "stranger": Peer(1, 6000, 6000, frozenset({100})),
    "uid-is-a-group": Peer(1, 7001, 6001, frozenset()),
    "groups-are-uids": Peer(1, 6002, 61003, frozenset({61001, 0})),
    "agent-in-a-human-group": Peer(1, 61002, 7001, frozenset({7001, 7002})),
    "in-the-sockets-groups": Peer(1, 6003, 7100, frozenset({7101, 7102})),
}
# Who may call each socket, written out from the manifest's clients.
ALLOWED = {
    "orch/api": {"researcher", "researcher-primary", "admin", "root"},
    "orch/svc": {"gw", "exec", "inject", "verify", "eval"},
    "orch/agent-session": {"agent", "agent-in-a-human-group"},
    "gw/agent": {"agent", "agent-in-a-human-group"},
    "gw/svc": {"orch"},
    "exec/agent": {"agent", "agent-in-a-human-group"},
    "exec/api": {"orch"},
    "inject/api": {"orch"},
    "verify/api": {"orch"},
}


@pytest.fixture
def principals() -> Principals:
    return load_principals(FIXTURES / "principals.json")


def test_every_socket_admits_exactly_its_clients(principals: Principals) -> None:
    seen = {}
    for sock in principals.sockets:
        allow = allowlist(principals, sock, GROUPS.__getitem__)
        seen[allow.socket] = {name for name, peer in CALLERS.items() if allow.allows(peer)}
    assert seen == ALLOWED


def test_infra_s_own_rendering_gives_the_same_allowlists() -> None:
    """principals.json as pokus-infrastructure renders it (other UIDs, a training job)."""
    rendered = load_principals(FIXTURES / "principals-infra.json")
    uid = {s.name: s.uid for s in rendered.services}
    callers = {name: Peer(1, uid[name], uid[name] + 1000, frozenset()) for name in uid}
    callers |= {name: CALLERS[name] for name in ("root", "researcher", "admin", "stranger")}
    callers["lora"] = Peer(1, rendered.jobs[0].uid, rendered.jobs[0].uid, frozenset())
    seen = {}
    for sock in rendered.sockets:
        allow = allowlist(rendered, sock, GROUPS.__getitem__)
        seen[allow.socket] = {name for name, peer in callers.items() if allow.allows(peer)}
    expected = {name: allowed & set(callers) for name, allowed in ALLOWED.items()}
    assert seen == expected


def update(principals: Principals, index: int, **clients: object) -> Principals:
    sock = principals.sockets[index]
    changed = sock.model_copy(update={"clients": sock.clients.model_copy(update=clients)})
    sockets = list(principals.sockets)
    sockets[index] = changed
    return principals.model_copy(update={"sockets": tuple(sockets)})


@pytest.mark.parametrize(
    ("index", "clients", "message"),
    [
        (
            1,
            {"accounts": ("potka-gw", "potka-ghost")},
            "not services of principals.json: potka-ghost",
        ),
        (
            1,
            {"accounts": ("potka-gw", "potka-train")},
            "not services of principals.json: potka-train",
        ),
        (
            1,
            {"accounts": ("potka-gw", "potka-agent")},
            "not dedicated to the agent names the agent",
        ),
        (2, {"root": True}, "dedicated to the agent names another client"),
        (2, {"humans": ("researchers",), "human_groups": ("pinka",)}, "dedicated to the agent"),
        (2, {"accounts": ("potka-agent", "potka-orch")}, "dedicated to the agent"),
        (
            0,
            {"human_groups": ("pinka", "no-such-group")},
            "^orch/api: unknown group 'no-such-group'$",
        ),
    ],
)
def test_an_allowlist_that_cannot_be_built_is_refused(
    principals: Principals, index: int, clients: dict[str, object], message: str
) -> None:
    job = Job(name="train", profile="gpu-trainer", account="potka-train", uid=61100)
    changed = update(principals, index, **clients).model_copy(update={"jobs": (job,)})
    with pytest.raises(PeerError, match=message):
        allowlist(changed, changed.sockets[index], GROUPS.__getitem__)


def test_groups_are_resolved_by_name(principals: Principals) -> None:
    assert _group_gid(grp.getgrgid(os.getgid()).gr_name) == os.getgid()
    with pytest.raises(KeyError):
        _group_gid("potka-no-such-group")
    changed = update(principals, 0, human_groups=("potka-no-such-group",))
    with pytest.raises(PeerError, match=r"^orch/api: unknown group 'potka-no-such-group'$"):
        allowlist(changed, changed.sockets[0])


@pytest.mark.parametrize("machine", ["x86_64", "aarch64"])
def test_x86_64_and_aarch64_are_supported(machine: str) -> None:
    check_architecture(machine)


@pytest.mark.parametrize("machine", ["riscv64", "ppc64le", "s390x", "armv7l", "mips64"])
def test_other_architectures_are_refused_by_name(machine: str) -> None:
    with pytest.raises(PeerError, match=f"unsupported architecture '{machine}'"):
        check_architecture(machine)


def test_the_peer_of_a_real_connection() -> None:
    check_architecture()
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with a, b:
        peer = peer_of(a)
    assert peer == Peer(os.getpid(), os.getuid(), os.getgid(), frozenset(os.getgroups()))


def test_peer_of_refuses_an_unsupported_architecture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.machine", lambda: "s390x")
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with a, b, pytest.raises(PeerError, match="'s390x'"):
        peer_of(a)


class Kernel:
    """A getsockopt that answers like the kernel for a caller with GROUPS."""

    def __init__(self, groups: list[int], cred: bytes | None = None, error: int = 0) -> None:
        self.groups = struct.pack(f"={len(groups)}I", *groups)
        self.cred = UCRED.pack(7, 2**32 - 2, 2**31 + 5) if cred is None else cred
        self.error = error
        self.lengths: list[int] = []

    def __call__(self, fd: int, level: int, option: int, length: int) -> bytes:
        if option == socket.SO_PEERCRED:
            return self.cred
        assert option == SO_PEERGROUPS
        self.lengths.append(length)
        if self.error:
            raise OSError(self.error, os.strerror(self.error))
        if length < len(self.groups):
            raise BufferTooSmall(len(self.groups))
        return self.groups


def test_many_groups_are_read_with_the_size_the_kernel_asks_for() -> None:
    kernel = Kernel(list(range(1000, 1300)))
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with a, b:
        peer = peer_of(a, kernel)
    assert peer == Peer(7, 2**32 - 2, 2**31 + 5, frozenset(range(1000, 1300)))
    assert kernel.lengths == [256, 1200]


@pytest.mark.parametrize(
    ("kernel", "kind", "error"),
    [
        (Kernel([1], cred=b"\0" * 8), PeerError, "SO_PEERCRED gave 8 bytes, not 12"),
        (
            Kernel([1] * (MAX_GROUPS + 1)),
            PeerError,
            f"SO_PEERGROUPS asked for {4 * (MAX_GROUPS + 1)} bytes",
        ),
        (Kernel([1], error=errno.ENOPROTOOPT), OSError, "Protocol not available"),
    ],
    ids=["short-cred", "too-many-groups", "no-peergroups"],
)
def test_an_unexpected_answer_is_an_error(
    kernel: Kernel, kind: type[Exception], error: str
) -> None:
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with a, b, pytest.raises(kind, match=error) as raised:
        peer_of(a, kernel)
    assert type(raised.value) is kind


@pytest.mark.parametrize(
    ("needed", "error"),
    [([1200, 1600], "asked for a larger buffer twice"), ([256], "asked for 256 bytes")],
    ids=["grows-twice", "not-larger"],
)
def test_a_kernel_that_keeps_asking_is_refused(needed: list[int], error: str) -> None:
    sizes = list(needed)

    def asking(fd: int, level: int, option: int, length: int) -> bytes:
        if option == socket.SO_PEERCRED:
            return UCRED.pack(1, 2, 3)
        raise BufferTooSmall(sizes.pop(0) if sizes else 99999)

    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with a, b, pytest.raises(PeerError, match=error):
        peer_of(a, asking)


@pytest.mark.skipif(not os.getgroups(), reason="the test process has no supplementary group")
def test_libc_getsockopt_says_how_large_a_buffer_the_groups_need() -> None:
    count = len(os.getgroups())
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with a, b:
        with pytest.raises(BufferTooSmall) as raised:
            _getsockopt(a.fileno(), socket.SOL_SOCKET, SO_PEERGROUPS, 4 * count - 4)
        assert raised.value.needed == 4 * count
        raw = _getsockopt(a.fileno(), socket.SOL_SOCKET, SO_PEERGROUPS, 4 * count)
    assert set(struct.unpack(f"={count}I", raw)) == set(os.getgroups())


def test_a_partial_group_is_an_error() -> None:
    def partial(fd: int, level: int, option: int, length: int) -> bytes:
        return UCRED.pack(1, 2, 3) if option == socket.SO_PEERCRED else b"\1\0\0\0\2"

    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with a, b, pytest.raises(PeerError, match="gave 5 bytes, not whole groups"):
        peer_of(a, partial)


MANY_GROUPS = """
import os, socket
from potka_core.peer import peer_of
groups = list(range(70000, 70300))
os.setgroups(groups)
a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
assert peer_of(a).groups == frozenset(groups), "groups differ"
print("read", len(groups))
"""


@pytest.mark.skipif(os.geteuid() != 0, reason="setting 300 supplementary groups needs root")
def test_a_real_caller_with_300_groups() -> None:
    done = subprocess.run(  # noqa: S603 (the test's own interpreter and script)
        [sys.executable, "-c", MANY_GROUPS], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == "read 300\n"
