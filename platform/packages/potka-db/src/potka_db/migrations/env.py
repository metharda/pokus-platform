"""Alembic's environment: online only, on the connection the caller gives.

The caller (:func:`potka_db.migrate.run`) opens the connection as the
migrator and puts it in the configuration's attributes. Every pending
revision runs in the one transaction begun here, committed after the last
one; a failure rolls it back and leaves the database as it was.
"""

from alembic import context
from sqlalchemy.engine import Connection

if context.is_offline_mode():
    raise RuntimeError("potka-db migrations run online only")
connection = context.config.attributes.get("connection")
if not isinstance(connection, Connection):
    raise RuntimeError("potka-db migrations need a connection in the configuration")
context.configure(connection=connection, transactional_ddl=True)
with context.begin_transaction():
    context.run_migrations()
