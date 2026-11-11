# PostgreSQL integration

Async ORM Kit uses SQLAlchemy's PostgreSQL async dialect through `asyncpg`. The
PostgreSQL driver is optional and is not imported by the package's core modules.

## Install

For a source checkout and the complete test suite:

```bash
uv sync --extra test --extra postgres
```

When installing from a published distribution:

```bash
pip install "async-orm-kit[postgres]"
```

## Connection URL

Use SQLAlchemy's async driver URL scheme:

```text
postgresql+asyncpg://USER:PASSWORD@HOST:PORT/DATABASE
```

For example, with a local PostgreSQL container:

```powershell
$env:ASYNC_ORM_KIT_POSTGRES_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/async_orm_kit_test"
uv run pytest tests/integration/test_postgres_async.py -v
```

The integration suite requires a disposable database. It creates and drops the
following tables as part of each test fixture:

- `phase5_postgres_workspaces`
- `phase5_postgres_documents`

Do not point the integration suite at a database containing application data.

## PostgreSQL integration contract

The live integration suite exercises:

- Native `UUID`, `JSONB`, and `ARRAY` values through model create, fetch, and filters.
- Server-generated timestamps and integer primary keys.
- `INSERT ... RETURNING` for bulk creation.
- `ON CONFLICT DO UPDATE` with a named unique-column target and returned rows.
- `ON CONFLICT DO NOTHING` and affected-row counts.
- `UPDATE ... RETURNING` and filtered DELETE.
- Caller-owned transactions, explicit rollback, and read-committed visibility
  between independent `AsyncSession` instances.
- Scalar joined loading and collection prefetch with query-count assertions.
- Parameterized PostgreSQL `EXPLAIN`, `EXPLAIN (VERBOSE)`, and `EXPLAIN (ANALYZE)`.
- PostgreSQL-specific query compilation, including `ILIKE`.
- Foreign-key `ON DELETE SET NULL` and unique-constraint error translation.

A feature is considered PostgreSQL-validated only after this suite passes against
the PostgreSQL service in CI. The existence of tests or successful SQL compilation
alone is not evidence of a live database integration pass.

## CI service

The repository workflow starts PostgreSQL 16 in a disposable GitHub Actions service
container, installs the `postgres` extra, verifies `asyncpg`, and runs the complete
suite with `ASYNC_ORM_KIT_POSTGRES_URL` configured. The local suite skips this module
when that variable is absent; CI explicitly requires it.

## Application ownership

The application remains responsible for creating the engine and session factory,
choosing pool settings, managing schema migrations, scoping sessions to requests or
units of work, and committing or rolling back transactions. An `AsyncSession` must
not be shared by concurrent asyncio tasks.
