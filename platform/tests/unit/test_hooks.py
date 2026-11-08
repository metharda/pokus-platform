"""The hooks print one JSON line each, with the key and exit status infra judges."""

from __future__ import annotations

import json

import pytest
from potka_orchestrator import hooks


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, object]]:
    status = hooks.main(list(argv))
    out, err = capsys.readouterr()
    assert err == ""
    lines = out.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert isinstance(payload, dict)
    return status, payload


def test_health_reports_the_release_as_not_healthy(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(capsys, "health") == (
        1,
        {"ok": False, "error": "no service of this release is implemented"},
    )


def test_reconcile_has_nothing_to_clean_up(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(capsys, "reconcile") == (0, {"ok": True, "reconciled": []})


def test_drain_has_nothing_in_flight(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(capsys, "drain", "--timeout", "30") == (0, {"drained": True})


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["migrate"],
        ["drain"],
        ["drain", "--timeout", "soon"],
    ],
)
def test_a_wrong_command_line_is_a_usage_error(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exited:
        hooks.main(argv)
    assert exited.value.code == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "usage: potka-hook" in err
