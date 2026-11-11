"""The shared fixtures, registered for every test run (``-p potka_testing.fixtures``)."""

from potka_testing.contract import contract_database, contract_passwords, migrated_database
from potka_testing.postgres import postgres_server, pytest_terminal_summary
from potka_testing.root import as_root

__all__ = [
    "as_root",
    "contract_database",
    "contract_passwords",
    "migrated_database",
    "postgres_server",
    "pytest_terminal_summary",
]
