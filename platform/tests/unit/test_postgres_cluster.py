"""The throwaway cluster's life: started, stopped, removed; a failed start keeps its cause."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from potka_testing import postgres
from potka_testing.postgres import Server, check_major, find_bindir, reachable_by_others

Run = Callable[[Sequence[str], object, Path], None]


def fake_run(calls: list[str], start_ok: bool = True) -> Run:
    def run(argv: Sequence[str], owner: object, cwd: Path) -> None:
        calls.append(f"{Path(argv[0]).name} {argv[-1]}")
        if argv[-1] == "start":
            assert "-c port=5432" in argv[argv.index("-o") + 1]
            log = Path(argv[argv.index("-l") + 1])
            log.write_text("FATAL:  the reason it did not start\n", encoding="utf-8")
            if not start_ok:
                raise RuntimeError("pg_ctl failed: could not start server")

    return run


@pytest.fixture
def bindir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(postgres, "server_major", lambda conninfo: 18)
    monkeypatch.setattr(postgres, "reachable_by_others", lambda path: None)
    return tmp_path / "bin"


def test_the_cluster_is_stopped_and_removed(bindir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(postgres, "_run", fake_run(calls))
    with postgres.local_cluster(bindir) as server:
        base = Path(server.params["host"]).parent
        assert (base / "data" / "pg_hba.conf").read_text() == postgres.HBA
        assert (server.params["port"], server.major) == ("5432", 18)
    assert calls == ["initdb -N", "pg_ctl start", "pg_ctl stop"]
    assert not base.exists()


def test_a_failed_start_reports_the_server_s_log_and_removes_the_cluster(
    bindir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(postgres, "_run", fake_run(calls, start_ok=False))
    made: list[Path] = []
    real = tempfile.mkdtemp

    def mkdtemp(**kwargs: Any) -> str:
        made.append(Path(real(**kwargs)))
        return str(made[-1])

    monkeypatch.setattr(tempfile, "mkdtemp", mkdtemp)
    with (
        pytest.raises(RuntimeError, match="the reason it did not start"),
        postgres.local_cluster(bindir),
    ):
        pass
    assert calls == ["initdb -N", "pg_ctl start"]
    assert len(made) == 1
    assert not made[0].exists()


def test_as_root_binaries_nobody_cannot_reach_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    locked = tmp_path / "locked"
    (locked / "bin").mkdir(parents=True)
    locked.chmod(0o700)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(RuntimeError, match=r"^nobody, who runs the test server, cannot search /"):
        with postgres.local_cluster(locked / "bin"):
            pass


def test_the_first_directory_others_cannot_search_is_found(tmp_path: Path) -> None:
    assert reachable_by_others(Path("/usr/bin")) is None
    locked = tmp_path / "a" / "b"
    locked.mkdir(parents=True)
    (tmp_path / "a").chmod(0o750)
    blocked = reachable_by_others(locked)
    assert blocked is not None
    assert blocked in [tmp_path / "a", *(tmp_path / "a").parents]


def test_a_server_of_another_major_fails_the_session() -> None:
    check_major(Server({}, 18), "18")
    check_major(Server({}, 16), None)
    with pytest.raises(pytest.fail.Exception, match=r"^the server's major version is 16, not 18$"):
        check_major(Server({}, 16), "18")


def test_the_variable_comes_first_and_a_system_major_needs_both_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "local"
    local.mkdir()
    (local / "initdb").touch()
    monkeypatch.setattr(postgres, "LOCAL_BINDIR", local)
    assert find_bindir({"POTKA_POSTGRES_BINDIR": "/elsewhere"}) == Path("/elsewhere")
    system = tmp_path / "system"
    (system / "17" / "bin").mkdir(parents=True)
    (system / "17" / "bin" / "initdb").touch()
    monkeypatch.setattr(postgres, "LOCAL_BINDIR", tmp_path / "none")
    monkeypatch.setattr(postgres, "SYSTEM_BINDIRS", system)
    assert find_bindir({}) is None
