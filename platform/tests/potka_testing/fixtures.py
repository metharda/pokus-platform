"""The shared fixtures, registered for every test run (``-p potka_testing.fixtures``)."""

import pytest

from potka_testing import opa
from potka_testing.contract import contract_database, contract_passwords, migrated_database
from potka_testing.infra import infra_checkout
from potka_testing.opa import opa_server
from potka_testing.postgres import postgres_server
from potka_testing.postgres import pytest_terminal_summary as database_summary
from potka_testing.root import as_root


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Name the server and the policy engine the tests used, also with ``-q``."""
    database_summary(terminalreporter)
    for line in opa.USED:
        terminalreporter.write_line(f"policy tests: {line}")


__all__ = [
    "as_root",
    "contract_database",
    "contract_passwords",
    "infra_checkout",
    "migrated_database",
    "opa_server",
    "postgres_server",
    "pytest_terminal_summary",
]
