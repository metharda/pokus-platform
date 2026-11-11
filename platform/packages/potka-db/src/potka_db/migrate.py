"""The ``migrate`` hook: bring the database to the release's newest revision.

pokus-infrastructure runs it at deployment, before the new release starts,
as the account ``potka-migrate``, which sees only the migrator's password
file and the database socket. It connects as the migrator role (the owner
of the schemas), applies every revision not applied yet in one
transaction (Alembic's, committed when the last revision ran), and prints
one JSON line on standard output: ``{"ok": true}``,
or ``{"ok": false, "error": "<type>"}`` with exit status 1. A failure is
written to standard error as a JSON log line with the exception's type and
frames, never its message: a database error can quote a row's values.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from potka_core.endpoints import PATH as ENDPOINTS
from potka_core.endpoints import load_endpoints
from potka_core.logging import JSONFormatter, JSONHandler, describe_exception

from potka_db.connection import MIGRATE, PASSWORD_DIR, connection_params, engine

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
LOGGER = "potka.migrate"


def alembic_config() -> Config:
    """Alembic's configuration, in code: the revisions of this package, no ini file.

    Returns:
        The configuration.
    """
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("path_separator", "os")
    return config


def head() -> str | None:
    """The newest revision of the release.

    Returns:
        Its identifier, or None when the release has no revision.
    """
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def run(params: dict[str, str]) -> None:
    """Apply every revision not applied yet, in one transaction.

    Args:
        params: The migrator's connection parameters.
    """
    config = alembic_config()
    with engine(params).connect() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "head")


def _log_failure(exc: BaseException) -> None:
    handler = JSONHandler(sys.stderr)
    handler.setFormatter(JSONFormatter(MIGRATE))
    logger = logging.getLogger(LOGGER)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.error("the migration failed", exc_info=(type(exc), exc, exc.__traceback__))
    finally:
        logger.removeHandler(handler)


def main() -> int:
    """Run the hook (the module's docstring).

    Returns:
        The exit status: 0 on success, 1 on failure.
    """
    try:
        run(connection_params(load_endpoints(ENDPOINTS), MIGRATE, PASSWORD_DIR))
    except Exception as exc:
        _log_failure(exc)
        sys.stdout.write(json.dumps({"error": describe_exception(exc), "ok": False}) + "\n")
        return 1
    sys.stdout.write(json.dumps({"ok": True}) + "\n")
    return 0
