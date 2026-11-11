"""Each socket-serving service, started as systemd starts it.

The service gets its sockets as descriptors from 3 on with LISTEN_PID,
LISTEN_FDS and LISTEN_FDNAMES, and runs with an audit hook that kills it
on any bind. principals.json admits the test's own user on one socket only
(by an account's UID, as root when the test runs as root, or by one of its
groups as a human class: a supplementary group when it has one); every
other socket must answer 403 on every route, and SIGTERM must end the
service with exit status 0, after a request in flight has its answer, or
once the grace period is over.
"""

from __future__ import annotations

import grp
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from potka_core.client import client
from potka_core.server import GRACE_SECONDS

FIXTURES = Path(__file__).parents[1] / "fixtures"
MODULES = {
    "orch": "potka_orchestrator",
    "gw": "potka_gateway",
    "exec": "potka_executor",
    "inject": "potka_injector",
    "verify": "potka_verifier",
}
HARNESS = """
import importlib, os, sys
from pathlib import Path

def refuse_bind(event, args):
    if event == "socket.bind":
        os.write(2, b"bind refused")
        os._exit(97)

sys.addaudithook(refuse_bind)
import potka_core.principals
potka_core.principals.PATH = Path(sys.argv[1])
sys.exit(importlib.import_module(sys.argv[2] + ".main").main())
"""
SLOW = """
import asyncio, sys
from pathlib import Path
import potka_core.principals
import potka_core.server
potka_core.principals.PATH = Path(sys.argv[1])
potka_core.server.GRACE_SECONDS = float(sys.argv[4])
from potka_core.logging import setup_logging
from potka_core.server import serve

async def slow(scope, receive, send):
    if scope["path"] == "/slow":
        Path(sys.argv[3]).write_text("received")
    await asyncio.sleep(1)
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"answered"})

setup_logging(sys.argv[2])
sys.exit(serve(sys.argv[2], lambda sock: slow))
"""
# (service, the socket that admits the test, how: an account, "group" or None)
CASES = [
    ("orch", "api", "group"),
    ("orch", "svc", "potka-gw"),
    ("orch", "agent-session", "potka-agent"),
    ("gw", "svc", "potka-orch"),
    ("gw", "agent", "potka-agent"),
    ("exec", "api", "potka-orch"),
    ("exec", "agent", "potka-agent"),
    ("inject", "api", "potka-orch"),
    ("inject", None, None),
    ("verify", "api", "potka-orch"),
    ("verify", None, None),
]
ROOT = os.getuid() == 0


def foreign_group() -> str:
    """A group this process is not in, for the human classes."""
    mine = {os.getgid(), *os.getgroups()}
    for group in grp.getgrall():
        if group.gr_gid not in mine:
            return group.gr_name
    pytest.fail("no group outside this process's groups")


def my_group() -> str:
    """A supplementary group of this process if it has one (read with SO_PEERGROUPS),
    else its primary group (read with SO_PEERCRED)."""
    for gid in os.getgroups():
        if gid != os.getgid():
            try:
                return grp.getgrgid(gid).gr_name
            except KeyError:
                continue
    return grp.getgrgid(os.getgid()).gr_name


def principals_for(tmp_path: Path, admitted: str | None, how: str | None) -> Path:
    data: dict[str, Any] = json.loads((FIXTURES / "principals.json").read_text())
    mine, foreign = my_group(), foreign_group()
    data["groups"] = {"admins": foreign, "researchers": mine if how == "group" else foreign}
    for entry in data["services"]:
        if entry["account"] == how and not ROOT:
            entry["uid"] = os.getuid()
    for sock in data["sockets"]:
        clients = sock["clients"]
        clients["human_groups"] = [data["groups"][human] for human in clients["humans"]]
        clients["root"] = ROOT and how not in ("group", None) and sock["name"] == admitted
        sock["fd_name"] = f"fd-{sock['name']}"  # systemd's names need not be the sockets'
        sock["path"] = str(tmp_path / f"{sock['service']}-{sock['name']}.sock")
    path = tmp_path / "principals.json"
    path.write_text(json.dumps(data))
    return path


def start(
    service: str, principals: Path, harness: str = HARNESS, arguments: tuple[str, ...] = ()
) -> tuple[subprocess.Popen[str], dict[str, str]]:
    mine = [s for s in json.loads(principals.read_text())["sockets"] if s["service"] == service]
    names = [s["fd_name"] for s in mine]
    listeners, paths = [], {}
    for entry in mine:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        paths[entry["name"]] = entry["path"]
        sock.bind(entry["path"])
        sock.listen()
        listeners.append(sock)

    def hand_over() -> None:
        for offset, sock in enumerate(listeners):
            os.dup2(sock.fileno(), 3 + offset)

    env = {**os.environ, "LISTEN_FDS": str(len(names)), "LISTEN_FDNAMES": ":".join(names)}
    proc = subprocess.Popen(  # noqa: S603 - a fixed argv: /bin/sh starting this Python
        [
            "/bin/sh",
            "-c",
            'LISTEN_PID=$$ exec "$0" "$@"',
            sys.executable,
            "-c",
            harness,
            str(principals),
            *(arguments or (MODULES[service],)),
        ],
        env=env,
        preexec_fn=hand_over,
        pass_fds=tuple(range(3, 3 + len(names))),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    for sock in listeners:
        sock.close()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and proc.poll() is None:
        try:
            with client(next(iter(paths.values())), timeout=5) as c:
                c.get("/healthz")
            return proc, paths
        except httpx.TransportError:
            time.sleep(0.1)
    proc.kill()
    out, err = proc.communicate()
    pytest.fail(f"{service} did not start: {out} {err}")


@pytest.mark.parametrize(("service", "admitted", "how"), CASES)
def test_the_service_answers_only_its_clients_and_stops_cleanly(
    tmp_path: Path, service: str, admitted: str | None, how: str | None
) -> None:
    if how == "potka-agent" and ROOT:
        pytest.skip("the agent's sockets admit only the agent's UID, which root cannot be")
    proc, paths = start(service, principals_for(tmp_path, admitted, how))
    try:
        answers = {}
        for name, path in paths.items():
            with client(path) as c:
                answers[name] = [
                    c.get(route).status_code for route in ("/healthz", "/readyz", "/x")
                ]
    finally:
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=GRACE_SECONDS + 15)
    expected = {name: [200, 503, 404] if name == admitted else [403, 403, 403] for name in paths}
    assert answers == expected
    assert proc.returncode == 0, (out, err)
    assert "bind refused" not in err
    lines = [json.loads(line) for line in out.splitlines()]
    assert lines[-1]["message"] == f"{service} stopped"
    assert {line["service"] for line in lines} == {service}


@pytest.mark.parametrize(
    ("grace", "answered"), [(GRACE_SECONDS, True), (0.2, False)], ids=["in-grace", "after-grace"]
)
def test_a_request_in_flight_after_sigterm(tmp_path: Path, grace: float, answered: bool) -> None:
    marker = tmp_path / "received"
    principals = principals_for(tmp_path, "svc", "potka-gw")
    proc, paths = start("orch", principals, SLOW, ("orch", str(marker), str(grace)))
    answers: list[httpx.Response | Exception] = []

    def request() -> None:
        try:
            answers.append(client(paths["svc"], timeout=20).get("/slow"))
        except httpx.HTTPError as exc:
            answers.append(exc)

    caller = threading.Thread(target=request)
    try:
        caller.start()
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        reached = marker.exists()
    finally:
        proc.send_signal(signal.SIGTERM)  # once, so uvicorn stops gracefully
        caller.join(timeout=20)
        try:
            out, err = proc.communicate(timeout=GRACE_SECONDS + 15)
        finally:
            proc.kill()
    assert reached, "the request never reached the service"
    if answered:
        assert [(a.status_code, a.text) for a in answers if isinstance(a, httpx.Response)] == [
            (200, "answered")
        ]
    else:
        assert len(answers) == 1
        assert not (isinstance(answers[0], httpx.Response) and answers[0].text == "answered")
    assert proc.returncode == 0, (out, err)
    assert json.loads(out.splitlines()[-1])["message"] == "orch stopped"
