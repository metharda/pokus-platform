---
title: "ADR-0001: The database is reached through SQLAlchemy Core and psycopg 3"
type: explanation
audience: [developer, reader]
applies_to: {platform: ">=0.0"}
last_verified: {date: null, rc: null, by: null}
---

# ADR-0001: The database is reached through SQLAlchemy Core and psycopg 3

- **Status:** accepted
- **Date:** 2026-11-11
- **Deciders:** pokus-platform maintainers

## Context

The platform keeps its records in one PostgreSQL database, in two schemas:
`public`, and `restricted`, which only the restricted-capable services may
read. The schema changes by migrations that the deployment runs before a
new release starts, and several services read and write the same tables.
Queries must never be assembled from strings that a caller controls, and
the tables the code expects must be the tables the migrations create.

The candidates were plain psycopg 3 with SQL written by hand, SQLAlchemy
Core (its expression language and table metadata, without the ORM) on
psycopg 3, and the SQLAlchemy ORM.

## Decision

1. Migrations are Alembic revisions, configured in code (no `alembic.ini`),
   and each revision's statements are fixed SQL. Applied revisions are
   never edited.
2. The code describes the tables with SQLAlchemy Core metadata and builds
   its queries with Core's expression language; it uses no ORM.
3. The driver is psycopg 3 with its binary package, so the lab host needs
   no libpq of its own.
4. A test migrates an empty database and compares the result with the
   metadata (Alembic's `compare_metadata`); any difference fails.

## Consequences

- Alembic depends on SQLAlchemy, so Core adds no further library, and the
  metadata both drives the queries and checks the migrations.
- Bound parameters and quoted identifiers come from Core and psycopg; no
  query is built by string formatting.
- The ORM's session, identity map and lazy loading stay out: every
  statement a service sends is visible where it is written, which keeps
  the reviews of grants and of the restricted schema concrete.
- Async code needs SQLAlchemy's asyncio extension and `greenlet`; they are
  added with the first service that queries from async code.
