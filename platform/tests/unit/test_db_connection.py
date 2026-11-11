"""Connection parameters from endpoints.toml alone, and the service's name as application_name."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from potka_core.endpoints import Endpoints, load_endpoints
from potka_core.logging import JSONFormatter
from potka_core.restricted import Restricted
from potka_core.settings import SettingsError
from potka_db.connection import application_name, connection_params

FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.fixture
def endpoints() -> Endpoints:
    return load_endpoints(FIXTURES / "endpoints.toml")


@pytest.fixture
def passwords(tmp_path: Path) -> Path:
    (tmp_path / "potka_gw").write_text("gw-password\n", encoding="utf-8")
    (tmp_path / "potka_migrator").write_text("migrator-password", encoding="utf-8")
    return tmp_path


def test_a_service_connects_through_the_socket_directory_as_its_role(
    endpoints: Endpoints, passwords: Path
) -> None:
    assert connection_params(endpoints, "gw", passwords) == {
        "host": "/run/pinka/postgres",
        "dbname": "potka",
        "user": "potka_gw",
        "password": "gw-password",
        "application_name": "potka-gw",
        "connect_timeout": "10",
    }


def test_the_migrate_hook_connects_as_the_migrator(endpoints: Endpoints, passwords: Path) -> None:
    params = connection_params(endpoints, "migrate", passwords)
    assert (params["user"], params["password"]) == ("potka_migrator", "migrator-password")
    assert params["application_name"] == "potka-migrate"


def test_a_service_without_a_role_is_refused(endpoints: Endpoints, passwords: Path) -> None:
    with pytest.raises(ValueError, match=r"^the service agent has no database role"):
        connection_params(endpoints, "agent", passwords)


def test_endpoints_without_postgres_are_refused(endpoints: Endpoints, passwords: Path) -> None:
    without = endpoints.model_copy(update={"postgres": None})
    with pytest.raises(ValueError, match=r"^endpoints\.toml has no postgres section$"):
        connection_params(without, "gw", passwords)


def test_a_missing_password_file_is_refused_without_its_path(
    endpoints: Endpoints, tmp_path: Path
) -> None:
    with pytest.raises(SettingsError) as raised:
        connection_params(endpoints, "exec", tmp_path)
    assert str(raised.value) == "the secret potka_exec cannot be read: FileNotFoundError"


class Name(str):
    """A str subclass, which could carry other behaviour."""


@pytest.mark.parametrize(
    ("service", "error"),
    [
        (Restricted("scenario-FIXTURE"), TypeError),
        (Name("orch"), TypeError),
        (b"orch", TypeError),
        ("Orch", ValueError),
        ("", ValueError),
        ("orch run-01", ValueError),
        ("orch\n", ValueError),
        ("a" * 11, ValueError),
        ("scenario-x", ValueError),
    ],
)
def test_application_name_is_a_service_name_only(service: object, error: type[Exception]) -> None:
    assert application_name("orch") == "potka-orch"
    assert application_name("x") == "potka-x"
    assert application_name("a" * 10) == "potka-" + "a" * 10
    with pytest.raises(error) as raised:
        application_name(service)  # type: ignore[arg-type]
    assert "scenario" not in str(raised.value)


def test_the_parameters_never_show_the_password(endpoints: Endpoints, passwords: Path) -> None:
    params = connection_params(endpoints, "gw", passwords)
    assert params["password"] == "gw-password"  # noqa: S105 (the fixture's password file)
    assert "gw-password" not in repr(params)
    assert "gw-password" not in str(params)
    assert "gw-password" not in f"{params}"
    record = logging.LogRecord("potka", logging.INFO, __file__, 1, "connecting %s", (params,), None)
    assert "gw-password" not in JSONFormatter("gw").format(record)


def test_no_pg_variable_survives_building_the_parameters(
    endpoints: Endpoints, passwords: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in [
        ("PGOPTIONS", "-c search_path=elsewhere"),
        ("PGSERVICE", "other"),
        ("PGAPPNAME", "run-01ABC"),
        ("PGSSLMODE", "disable"),
    ]:
        monkeypatch.setenv(name, value)
    connection_params(endpoints, "gw", passwords)
    assert [name for name in os.environ if name.startswith("PG")] == []
