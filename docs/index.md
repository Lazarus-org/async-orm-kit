# Async ORM Kit

An asynchronous, Django-inspired QuerySet layer for SQLAlchemy 2.x that keeps
engine and transaction ownership in the application.

See the [public README](../README.md) for installation and examples, or build
the [complete documentation](index.rst).

## Core concepts

- Pass SQLAlchemy `AsyncSession` explicitly to model managers.
- Build queries using chainable QuerySets, expressions, and relationship loading.
- Configure the query planner with explicit, immutable `ORMConfig`.
- Install optional extras only for integrations you use.

## Getting started

Start with [AsyncSession integration](integrations/async-session.md) and
then review the [Advanced SQL reference](integrations/advanced-sql.md), PostgreSQL,
MySQL, storage, Alembic, and FastAPI guides.

For development diagnostics see [query observability](integrations/observability.md).
