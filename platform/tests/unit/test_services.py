"""The entry points: stubs fail at once; serving services need their sockets."""

from __future__ import annotations

import importlib
import json
import logging
import signal
import sys
import threading
import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest

STUBS = {"potka_agent": "agent", "potka_evaluator": "eval"}
SERVING = {
    "potka_orchestrator": "orch",
    "potka_gateway": "gw",
    "potka_executor": "exec",
    "potka_injector": "inject",
    "potka_verifier": "verify",
}


@pytest.mark.parametrize(("module", "service"), sorted(STUBS.items()))
def test_entry_point_logs_one_json_line_and_fails(
    module: str, service: str, capsys: pytest.CaptureFixture[str]
) -> None:
    main = importlib.import_module(f"{module}.main")
    assert main.main() == 1
    out, err = capsys.readouterr()
    assert err == ""
    lines = out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "level": "error",
        "service": service,
        "message": "not implemented in this release",
    }


@pytest.fixture
def root_logging(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Restore what setup_logging and serve change in the test process."""
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", root.handlers[:])
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    monkeypatch.setattr(sys, "unraisablehook", sys.unraisablehook)
    monkeypatch.setattr(warnings, "showwarning", warnings.showwarning)
    level, sigterm = root.level, signal.getsignal(signal.SIGTERM)
    try:
        yield
    finally:
        root.setLevel(level)
        signal.signal(signal.SIGTERM, sigterm)


@pytest.mark.parametrize(("module", "service"), sorted(SERVING.items()))
@pytest.mark.usefixtures("root_logging")
def test_a_serving_service_without_its_files_and_sockets_fails(
    module: str,
    service: str,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "principals.json"
    monkeypatch.setattr("potka_core.principals.PATH", missing)
    main = importlib.import_module(f"{module}.main")
    assert main.main() == 1
    out, err = capsys.readouterr()
    assert err == ""
    lines = [json.loads(line) for line in out.splitlines()]
    assert len(lines) == 1
    assert (lines[0]["level"], lines[0]["service"]) == ("error", service)
    assert lines[0]["message"] == (
        f"{service} cannot start: [Errno 2] No such file or directory: '{missing}'"
    )
