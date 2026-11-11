Alembic migrations
==================

Alembic integration is optional. ``get_target_metadata()`` imports the
application's trusted model modules before exposing the shared declarative
metadata used by ``async_orm_kit.Model``.

.. code-block:: console

   python -m pip install "async-orm-kit[migrations]"

Configure metadata in ``alembic/env.py``:

.. code-block:: python

   from async_orm_kit.integrations.alembic import get_target_metadata

   target_metadata = get_target_metadata(
       "myapp.models",        # replace with the real model module
   )

Import every model-defining module. A missing import can cause Alembic
``--autogenerate`` to see an incomplete schema. Invalid module imports
raise rather than silently succeeding.

Async Alembic migration pattern
-------------------------------

Alembic's migration operations use a synchronous context bridged through
``AsyncConnection.run_sync``. The following is the essential online pattern;
complete the project's Alembic ``env.py`` with its existing offline and
logging configuration:

.. code-block:: python

   import asyncio
   from alembic import context
   from sqlalchemy.ext.asyncio import create_async_engine

   def run_sync_migrations(connection):
       context.configure(connection=connection, target_metadata=target_metadata,
                         compare_type=True)
       with context.begin_transaction():
           context.run_migrations()

   async def run_online(url):
       engine = create_async_engine(url)
       try:
           async with engine.connect() as conn:
               await conn.run_sync(run_sync_migrations)
       finally:
           await engine.dispose()

   # Called by Alembic's CLI entrypoint (not from an already-running event loop):
   # asyncio.run(run_online(database_url))

.. code-block:: console

   alembic revision --autogenerate -m "initial schema"
   alembic upgrade head
   alembic check

Autogenerate output is a proposal requiring human review. Rename detection,
data migrations, destructive operations and dialect-specific DDL require
manual attention. The package does not ship an application migration history
or run migrations for you.
