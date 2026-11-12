"""A real connection's metadata names the service only, whatever the environment holds."""

from __future__ import annotations

import pytest
from potka_db.connection import application_name, engine
from potka_testing.contract import Database
from sqlalchemy import text

SERVICES = ["orch", "gw", "exec", "inject", "verify", "eval"]


@pytest.mark.parametrize("service", SERVICES)
def test_the_connection_s_application_name_is_the_service_s_name(
    contract_database: Database, service: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # libpq would take these from the environment; none may reach the server.
    monkeypatch.setenv("PGAPPNAME", "run-01HZZZZZZZZZZZZZZZZZZZZZZZ scenario-FIXTURE")
    monkeypatch.setenv("PGOPTIONS", "-c search_path=scenario_fixture -c work_mem=7777kB")
    params = {
        key: str(value) for key, value in contract_database.params(f"potka_{service}").items()
    }
    params["application_name"] = application_name(service)
    with engine(params).connect() as conn:
        row = conn.execute(
            text(
                "SELECT application_name, usename, current_setting('search_path'),"
                " current_setting('work_mem') FROM pg_stat_activity WHERE pid = pg_backend_pid()"
            )
        ).one()
    assert tuple(row) == (f"potka-{service}", f"potka_{service}", '"$user", public', "4MB")
