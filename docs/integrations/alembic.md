# Alembic integration

Alembic is an optional migration tool. Async ORM Kit does not run migrations,
create tables, or own migration configuration. It exposes the shared SQLAlchemy
metadata and a small helper to ensure application model modules are imported before
autogeneration.

## Install the optional migration dependency

```bash
pip install "async-orm-kit[migrations]"
```

The `migrations` extra installs Alembic. It does not add Alembic to the package's
mandatory runtime dependencies.

## Configure target metadata

Import every module that declares mapped models before Alembic inspects metadata:

```python
# alembic/env.py
from async_orm_kit.integrations.alembic import get_target_metadata

target_metadata = get_target_metadata(
    "myapp.models",          # package __init__ imports all mapped model modules
    # "myapp.accounts.models",  # add separate model modules when needed
)
```

`get_target_metadata()` imports the supplied trusted module names in order and
returns `async_orm_kit.models.meta.Base.metadata`. Import failures propagate; the
helper will not silently return incomplete metadata. If the application already
imports all models, `target_metadata = get_target_metadata()` is sufficient.

If your project uses several declarative bases, configure Alembic with the
appropriate metadata collection yourself; this helper intentionally returns only
Async ORM Kit's shared `Base.metadata`.

## Async engine environment

Use Alembic's async-engine pattern and bridge its synchronous migration context
through `AsyncConnection.run_sync()`:

```python
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from async_orm_kit.integrations.alembic import get_target_metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = get_target_metadata("myapp.models")


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=False,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online():
    asyncio.run(run_async_migrations())
```

Keep your actual database URL in environment/secrets configuration rather than
committing credentials to `alembic.ini`. The example assumes Alembic's command-line
entry point owns the event loop. Do not call `asyncio.run()` from an already-running
application event loop.

For offline SQL generation, configure Alembic with the URL and `target_metadata`
using its standard offline migration pattern. Offline mode does not connect to or
validate the target database.

## Autogenerate and review

```bash
alembic revision --autogenerate -m "create application tables"
alembic upgrade head
alembic check
```

Autogenerate is a proposal, not a substitute for migration review. Review data
backfills, constraint changes, server defaults, indexes, renames, and destructive
operations manually. Alembic cannot infer that a dropped column and a new column
represent a rename. Test upgrades and downgrades against a disposable database for
every supported dialect.

## Integration contract

The package integration tests use Alembic's `MigrationContext` and
`compare_metadata()` against SQLite to verify metadata discovery and schema-drift
detection. They do not claim that every generated migration is portable across
SQLite, PostgreSQL, and MySQL. Each production dialect still requires its own
migration test job and reviewed migration scripts.
