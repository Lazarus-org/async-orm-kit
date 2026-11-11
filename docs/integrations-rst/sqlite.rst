SQLite / aiosqlite
==================

Install the driver with ``async-orm-kit[sqlite]`` and create a SQLAlchemy
async engine:

.. code-block:: python

   from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

   engine = create_async_engine("sqlite+aiosqlite:///./app.db")
   SessionFactory = async_sessionmaker(engine, expire_on_commit=False)

For an isolated development schema:

.. code-block:: python

   async with engine.begin() as connection:
       await connection.run_sync(Model.metadata.create_all)

SQLite is valuable for local tests, but its concurrency, row locking and
some JSON semantics differ from PostgreSQL and MySQL. ``select_for_update``
does not provide row locking and is explicitly rejected. In-memory SQLite
lifetime depends on the engine and connection configuration.

Run the native test suites after installing ``aiosqlite``; skipped modules
do not count as verified runtime behavior. Test JSON path extraction, query
streaming, session rollback and deferred-field refresh against a real
``AsyncSession``.

For transaction examples see :doc:`../guide/sessions`.
