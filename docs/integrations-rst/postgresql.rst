PostgreSQL / asyncpg
====================

Install ``async-orm-kit[postgres]``. Configure an async engine:

.. code-block:: python

   engine = create_async_engine(
       "postgresql+asyncpg://user:password@localhost/app",
       pool_pre_ping=True,
   )

PostgreSQL is the target for:

* native JSONB and ARRAY mapped columns and their ORM operators;
* row locking with supported ``FOR UPDATE`` options;
* conflict-target-aware bulk upserts and insert ``RETURNING``;
* compiled ``EXPLAIN`` with PostgreSQL-specific options.

Bulk insert example
-------------------

.. code-block:: python

   async with SessionFactory() as session:
       async with session.begin():
           inserted = await User.objects.bulk_create(
               session,
               [{"email": "a@example.com"}, {"email": "b@example.com"}],
               returning=True,
           )

``returning=True`` returns mapped inserted objects in supported modes. Upserts
require specifying appropriate conflict columns/fields; cross-database
``unique_fields`` semantics aren't interchangeable with MySQL.

Live database tests
-------------------

The repository's PostgreSQL suites are configured to use
``ASYNC_ORM_KIT_POSTGRES_URL``. Use a dedicated disposable database whose
**name ends in ``_test``**; destructive create/drop setup must not point at
production. The CI database service and provider tests must run without
skips before claiming tested support.

.. code-block:: console

   export ASYNC_ORM_KIT_POSTGRES_URL='postgresql+asyncpg://user:password@localhost/orm_test'
   pytest -q tests/integration/test_postgres_async.py
   pytest -q tests/integration/test_advanced_postgres_async.py

Check exact module names in the current ``tests/integration`` tree when
running individual suites. The current local validation did not execute
these live tests.
