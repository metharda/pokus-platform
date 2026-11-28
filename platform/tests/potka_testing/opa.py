"""The lab's OPA for the tests: the binary of ``make opa``, serving a bundle on the loopback.

The binary is the one ``tests/opa/opa.lock`` names, and it is run only
after its sha256 was compared with the lock's. Without it a test that
needs it skips (run ``make opa``); in CI a skip fails the job. A binary
that is there and is not the locked one fails the test: it is never run,
and it is not what ``make opa`` installs.

The server runs as the lab runs its policy engine, as far as the lab's
documentation says: with authorization, under a rule that allows only
decision queries at exactly the decision's path, and health. The rule
here is a stand-in written from those words; the lab's own is not read.
"""

from __future__ import annotations

import contextlib
import socket
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from potka_ci import policy

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / "platform" / "policy"
DECISION_PATH = "potka/action/decision"
STARTS_WITHIN = 20.0
# A port that was free can be taken before the server binds it: another is tried.
PORTS_TRIED = 3
AUTHORIZATION = """package system.authz

default allow := false

allow if {
	input.method in {"GET", "POST"}
	input.path == ["v1", "data", "potka", "action", "decision"]
}

allow if {
	input.method == "GET"
	input.path == ["health"]
}
"""
USED: list[str] = []


def binary() -> Path:
    """The lab's OPA, verified; the test skips without it and fails on another binary."""
    there = (ROOT / policy.BINARY).exists()
    try:
        found = policy.verified_binary(ROOT)
    except policy.NoOpa as exc:
        if there:
            pytest.fail(f"{exc}")
        pytest.skip(f"{exc}")
    version, _ = policy.locked(ROOT)
    line = f"OPA {version} of platform/tests/opa/opa.lock"
    if line not in USED:
        USED.append(line)
    return found


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextlib.contextmanager
def server(bundle: Path = BUNDLE) -> Iterator[str]:
    """Serve BUNDLE under the stand-in authorization; gives the server's address.

    Args:
        bundle: A bundle directory with a manifest that names its roots.

    Raises:
        RuntimeError: The server did not answer its health check in time;
            the error holds what it printed.
    """
    opa = binary()
    with tempfile.TemporaryDirectory(prefix="potka-opa-") as work:
        rule = Path(work) / "authz"
        rule.mkdir()
        (rule / "authz.rego").write_text(AUTHORIZATION, encoding="utf-8")
        (rule / ".manifest").write_text('{"roots": ["system"]}\n', encoding="utf-8")
        log = Path(work) / "server.log"
        process, address = _started(opa, bundle, rule, log, work)
        try:
            yield f"http://{address}"
        finally:
            _stopped(process)


def _started(
    opa: Path, bundle: Path, rule: Path, log: Path, work: str
) -> tuple[subprocess.Popen[bytes], str]:
    """Start the server on a free port; when it ends at once, on another, a few times."""
    for _ in range(PORTS_TRIED):
        address = f"127.0.0.1:{_free_port()}"
        command = [
            str(opa),
            "run",
            "--server",
            f"--addr={address}",
            "--authorization=basic",
            "--log-level=error",
            "--bundle",
            str(bundle),
            str(rule),
        ]
        with log.open("wb") as printed:
            process = subprocess.Popen(  # noqa: S603
                command,
                stdout=printed,
                stderr=subprocess.STDOUT,
                cwd=work,
                env={"PATH": "/usr/bin:/bin", "HOME": work},
            )
        deadline = time.monotonic() + STARTS_WITHIN
        with httpx.Client(trust_env=False, timeout=1.0) as client:
            while process.poll() is None and time.monotonic() <= deadline:
                try:
                    if client.get(f"http://{address}/health").status_code == 200:
                        return process, address
                except httpx.HTTPError:
                    pass
                time.sleep(0.05)
        ended = process.poll() is not None
        _stopped(process)
        if not ended:
            break
    said = log.read_text(encoding="utf-8", errors="replace")
    raise RuntimeError(f"OPA did not start:\n{said}")


def _stopped(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


@pytest.fixture(scope="session")
def opa_server() -> Iterator[str]:
    """The address of an OPA that serves the release's bundle, for the whole run."""
    with server() as address:
        yield address
