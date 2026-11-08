"""Every service's entry point logs that it is not implemented and fails."""

from __future__ import annotations

import importlib
import json

import pytest

SERVICES = {
    "potka_orchestrator": "orch",
    "potka_agent": "agent",
    "potka_gateway": "gw",
    "potka_executor": "exec",
    "potka_injector": "inject",
    "potka_verifier": "verify",
    "potka_evaluator": "eval",
}


@pytest.mark.parametrize(("module", "service"), sorted(SERVICES.items()))
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
