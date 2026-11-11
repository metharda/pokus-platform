"""The integration tests' PostgreSQL server: reachable, the required major, password logins."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import psycopg
import pytest
from potka_testing.postgres import Server, find_bindir
from psycopg import sql


def test_the_superuser_reaches_the_server(postgres_server: Server) -> None:
    with postgres_server.connect() as conn:
        row = conn.execute("SELECT current_user, current_setting('is_superuser')").fetchone()
    assert row == ("postgres", "on")
    assert postgres_server.major >= 16
    required = os.environ.get("POTKA_POSTGRES_REQUIRED")
    if required:
        assert postgres_server.major == int(required)


def test_a_role_logs_in_with_its_password_and_only_with_it(postgres_server: Server) -> None:
    password = secrets.token_hex(16)
    with postgres_server.connect() as conn:
        conn.execute(sql.SQL("CREATE ROLE potka_test_login LOGIN PASSWORD {}").format(password))
    try:
        with postgres_server.connect(user="potka_test_login", password=password) as conn:
            assert conn.execute("SELECT current_user").fetchone() == ("potka_test_login",)
        with pytest.raises(psycopg.OperationalError, match="password authentication failed"):
            postgres_server.connect(user="potka_test_login", password="not-" + password)
    finally:
        with postgres_server.connect() as conn:
            conn.execute("DROP ROLE potka_test_login")


def test_the_binaries_come_from_the_variable_then_make_postgres_then_the_newest_system_major(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert find_bindir({"POTKA_POSTGRES_BINDIR": str(tmp_path)}) == tmp_path
    system = tmp_path / "system"
    for major in ("9", "16", "18", "x"):
        (system / major / "bin").mkdir(parents=True)
        for tool in ("initdb", "pg_ctl"):
            (system / major / "bin" / tool).touch()
    monkeypatch.setattr("potka_testing.postgres.LOCAL_BINDIR", tmp_path / "none")
    monkeypatch.setattr("potka_testing.postgres.SYSTEM_BINDIRS", system)
    assert find_bindir({}) == system / "18" / "bin"
    local = tmp_path / "local"
    local.mkdir()
    (local / "initdb").touch()
    monkeypatch.setattr("potka_testing.postgres.LOCAL_BINDIR", local)
    assert find_bindir({}) == local
    monkeypatch.setattr("potka_testing.postgres.LOCAL_BINDIR", tmp_path / "none")
    monkeypatch.setattr("potka_testing.postgres.SYSTEM_BINDIRS", tmp_path / "empty")
    assert find_bindir({}) is None
