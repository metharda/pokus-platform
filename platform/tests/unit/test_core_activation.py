"""Sockets handed over by systemd: LISTEN_PID, LISTEN_FDS, LISTEN_FDNAMES."""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest
from potka_core.activation import ActivationError, inherited_sockets, service_sockets
from potka_core.principals import Principals, load_principals

FIXTURES = Path(__file__).parents[1] / "fixtures"
FIRST = 600  # far above the descriptors pytest uses


def handed_over(tmp_path: Path, *kinds: str) -> list[int]:
    """Put sockets of KINDS at descriptors FIRST, FIRST + 1, ..., as systemd would."""
    fds = []
    for offset, kind in enumerate(kinds):
        if kind == "closed":
            fds.append(FIRST + offset)
            continue
        if kind == "file":
            with (tmp_path / f"{offset}.txt").open("w") as file:
                os.dup2(file.fileno(), FIRST + offset)
            fds.append(FIRST + offset)
            continue
        family = socket.AF_INET if kind.startswith("tcp") else socket.AF_UNIX
        kind_of = socket.SOCK_SEQPACKET if kind == "seqpacket" else socket.SOCK_STREAM
        sock = socket.socket(family, kind_of)
        if kind in ("listening", "seqpacket"):
            sock.bind(str(tmp_path / f"{offset}.sock"))
        if kind == "tcp-listening":
            sock.bind(("127.0.0.1", 0))
        if kind in ("listening", "seqpacket", "tcp-listening"):
            sock.listen()
        os.dup2(sock.fileno(), FIRST + offset)
        sock.close()
        fds.append(FIRST + offset)
    return fds


@pytest.fixture(autouse=True)
def close_handed_over() -> Iterator[None]:
    yield
    for fd in range(FIRST, FIRST + 4):
        try:
            os.close(fd)
        except OSError:
            pass


def env(**changes: str) -> dict[str, str]:
    base = {
        "LISTEN_PID": str(os.getpid()),
        "LISTEN_FDS": "2",
        "LISTEN_FDNAMES": "api:svc",
        "OTHER": "x",
    }
    return {**base, **changes}


def test_the_sockets_by_name_and_the_variables_removed(tmp_path: Path) -> None:
    handed_over(tmp_path, "listening", "listening")
    environ = env()
    sockets = inherited_sockets(environ, FIRST)
    assert sorted(sockets) == ["api", "svc"]
    assert sockets["api"].getsockname() == str(tmp_path / "0.sock")
    assert sockets["svc"].getsockname() == str(tmp_path / "1.sock")
    assert not any(sock.get_inheritable() for sock in sockets.values())
    assert environ == {"OTHER": "x"}


@pytest.mark.parametrize(
    ("changes", "kinds", "message"),
    [
        ({"LISTEN_PID": "1"}, ("listening", "listening"), "LISTEN_PID"),
        ({"LISTEN_FDS": "3"}, ("listening", "listening"), "disagree"),
        ({"LISTEN_FDS": "two"}, ("listening", "listening"), "disagree"),
        ({"LISTEN_FDS": "\u00b2"}, ("listening", "listening"), "disagree"),
        ({"LISTEN_FDS": " 2"}, ("listening", "listening"), "disagree"),
        ({"LISTEN_FDNAMES": "api:api"}, ("listening", "listening"), "twice"),
        ({}, ("listening", "unbound"), "svc is not a listening Unix stream socket"),
        ({}, ("listening", "tcp"), "svc is not a listening Unix stream socket"),
        ({}, ("listening", "tcp-listening"), "svc is not a listening Unix stream socket"),
        ({}, ("listening", "seqpacket"), "svc is not a listening Unix stream socket"),
        ({}, ("listening", "closed"), r"descriptor 601 \(svc\) is not a socket"),
        ({}, ("listening", "file"), r"descriptor 601 \(svc\) is not a socket"),
    ],
)
def test_wrong_hand_overs_are_refused(
    tmp_path: Path, changes: dict[str, str], kinds: tuple[str, ...], message: str
) -> None:
    handed_over(tmp_path, *kinds)
    environ = env(**changes)
    with pytest.raises(ActivationError, match=message):
        inherited_sockets(environ, FIRST)
    assert environ == {"OTHER": "x"}


def test_without_systemd_nothing_is_handed_over() -> None:
    with pytest.raises(ActivationError, match="no sockets from systemd"):
        inherited_sockets({}, FIRST)


def in_tmp(tmp_path: Path) -> Principals:
    """principals.json with orch's sockets in TMP_PATH and fd_names unlike the names."""
    principals = load_principals(FIXTURES / "principals.json")
    sockets = tuple(
        s.model_copy(update={"path": str(tmp_path / f"{s.name}.sock"), "fd_name": f"fd-{s.name}"})
        if s.service == "orch"
        else s
        for s in principals.sockets
    )
    return principals.model_copy(update={"sockets": sockets})


def listening(tmp_path: Path, names: list[str], at: str = "{}") -> dict[str, socket.socket]:
    sockets = {}
    for name in names:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(tmp_path / f"{at.format(name.removeprefix('fd-'))}.sock"))
        sock.listen()
        sockets[name] = sock
    return sockets


@pytest.mark.parametrize(
    ("names", "message"),
    [
        (["fd-api", "fd-svc"], r"sockets missing: \['fd-agent-session'\], not the service's: \[\]"),
        (["fd-api", "fd-svc", "fd-agent-session", "extra"], r"not the service's: \['extra'\]"),
        (["api", "svc", "agent-session"], r"sockets missing: \['fd-agent-session', 'fd-api'"),
    ],
)
def test_the_service_gets_exactly_its_sockets(
    tmp_path: Path, names: list[str], message: str
) -> None:
    sockets = listening(tmp_path, names)
    with pytest.raises(ActivationError, match=message):
        service_sockets(in_tmp(tmp_path), "orch", sockets)
    for sock in sockets.values():
        sock.close()


def test_each_socket_is_found_by_its_fd_name_and_listens_on_its_path(tmp_path: Path) -> None:
    principals = in_tmp(tmp_path)
    sockets = listening(tmp_path, ["fd-api", "fd-svc", "fd-agent-session"])
    found = service_sockets(principals, "orch", sockets)
    assert {s.name: sock for s, sock in found.items()} == {
        "api": sockets["fd-api"],
        "svc": sockets["fd-svc"],
        "agent-session": sockets["fd-agent-session"],
    }
    for sock in sockets.values():
        sock.close()
    elsewhere = listening(tmp_path, ["fd-api", "fd-svc", "fd-agent-session"], at="other-{}")
    with pytest.raises(ActivationError, match=r"the socket fd-api listens on .*other-api\.sock"):
        service_sockets(principals, "orch", elsewhere)
    for sock in elsewhere.values():
        sock.close()
