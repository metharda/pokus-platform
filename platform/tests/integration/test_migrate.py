"""The migrate hook: the migrator applies the release's revisions; one JSON line either way."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import sqlalchemy.exc
from potka_db import migrate
from potka_testing.contract import Database

FIXTURES = Path(__file__).parents[1] / "fixtures"


def migrator(database: Database) -> dict[str, str]:
    return {key: str(value) for key, value in database.params("potka_migrator").items()}


def applied(database: Database) -> list[str]:
    with database.connect("potka_migrator") as conn:
        return [row[0] for row in conn.execute("SELECT version_num FROM public.alembic_version")]


def test_the_migrator_brings_a_fresh_database_to_the_newest_revision(
    contract_database: Database,
) -> None:
    migrate.run(migrator(contract_database))
    head = migrate.head()
    assert applied(contract_database) == ([] if head is None else [head])
    migrate.run(migrator(contract_database))
    assert applied(contract_database) == ([] if head is None else [head])


def test_the_hook_reads_endpoints_and_the_migrator_s_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "potka_migrator").write_text("migrator-password\n", encoding="utf-8")
    monkeypatch.setattr(migrate, "ENDPOINTS", FIXTURES / "endpoints.toml")
    monkeypatch.setattr(migrate, "PASSWORD_DIR", tmp_path)
    seen: list[dict[str, str]] = []
    monkeypatch.setattr(migrate, "run", seen.append)
    assert migrate.main() == 0
    assert capsys.readouterr() == ('{"ok": true}\n', "")
    assert seen == [
        {
            "host": "/run/pinka/postgres",
            "dbname": "potka",
            "user": "potka_migrator",
            "password": "migrator-password",
            "application_name": "potka-migrate",
            "connect_timeout": "10",
        }
    ]


def test_the_hook_succeeds_on_a_real_database(
    contract_database: Database,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(migrate, "connection_params", lambda *_: migrator(contract_database))
    monkeypatch.setattr(migrate, "load_endpoints", lambda _: None)
    assert migrate.main() == 0
    assert capsys.readouterr() == ('{"ok": true}\n', "")


def test_a_failure_prints_its_type_and_writes_no_message(
    contract_database: Database,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    params = migrator(contract_database)
    params["password"] = "wrong-" + params["password"]
    monkeypatch.setattr(migrate, "connection_params", lambda *_: params)
    monkeypatch.setattr(migrate, "load_endpoints", lambda _: None)
    assert migrate.main() == 1
    out, err = capsys.readouterr()
    assert json.loads(out) == {"ok": False, "error": "sqlalchemy.exc.OperationalError"}
    assert out.count("\n") == 1
    [line] = err.splitlines()
    logged = json.loads(line)
    assert logged["message"] == "the migration failed"
    assert "psycopg.OperationalError (message not written)" in logged["exception"]
    assert "password" not in line
    assert params["password"] not in line


def test_a_missing_password_file_is_a_failure_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(migrate, "ENDPOINTS", FIXTURES / "endpoints.toml")
    monkeypatch.setattr(migrate, "PASSWORD_DIR", tmp_path)
    assert migrate.main() == 1
    out, err = capsys.readouterr()
    assert json.loads(out) == {"ok": False, "error": "potka_core.settings.SettingsError"}
    assert str(tmp_path) not in out + err


REVISION = """from alembic import op

revision = "{revision}"
down_revision = {down}
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("{statement}")
"""


@pytest.fixture
def failing_revisions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The release's environment with two revisions: a table, then a statement that fails."""
    scripts = tmp_path / "migrations"
    (scripts / "versions").mkdir(parents=True)
    shutil.copy(migrate.MIGRATIONS / "env.py", scripts / "env.py")
    first = REVISION.format(
        revision="t1", down="None", statement="CREATE TABLE public.migrate_probe (x int)"
    )
    second = REVISION.format(
        revision="t2",
        down='"t1"',
        statement="INSERT INTO public.nowhere VALUES ('value-not-written')",
    )
    (scripts / "versions" / "t1.py").write_text(first, encoding="utf-8")
    (scripts / "versions" / "t2.py").write_text(second, encoding="utf-8")
    monkeypatch.setattr(migrate, "MIGRATIONS", scripts)
    return scripts


def test_every_pending_revision_runs_in_one_transaction(
    contract_database: Database, failing_revisions: Path
) -> None:
    with pytest.raises(sqlalchemy.exc.ProgrammingError):
        migrate.run(migrator(contract_database))
    with contract_database.connect("potka_migrator") as conn:
        left = conn.execute(
            "SELECT to_regclass('public.migrate_probe'), to_regclass('public.alembic_version')"
        ).fetchone()
    assert left == (None, None)


def test_a_failed_statement_s_text_and_values_are_never_written(
    contract_database: Database,
    failing_revisions: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(migrate, "connection_params", lambda *_: migrator(contract_database))
    monkeypatch.setattr(migrate, "load_endpoints", lambda _: None)
    for _ in range(2):
        assert migrate.main() == 1
        out, err = capsys.readouterr()
        assert json.loads(out) == {"ok": False, "error": "sqlalchemy.exc.ProgrammingError"}
        assert len(err.splitlines()) == 1
        for secret in ("value-not-written", "nowhere", "INSERT"):
            assert secret not in out + err


def test_the_migrator_owns_the_version_table(contract_database: Database) -> None:
    migrate.run(migrator(contract_database))
    with contract_database.connect("potka_migrator") as conn:
        owner = conn.execute(
            "SELECT pg_get_userbyid(relowner) FROM pg_class"
            " WHERE oid = 'public.alembic_version'::regclass"
        ).fetchone()
    assert owner == ("potka_migrator",)
