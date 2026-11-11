MySQL / asyncmy
===============

Install ``async-orm-kit[mysql]`` and use a MySQL 8.x server and the ``asyncmy``
driver:

.. code-block:: python

   engine = create_async_engine(
       "mysql+asyncmy://user:password@localhost/app?charset=utf8mb4",
       pool_pre_ping=True,
   )

MySQL-specific writes
---------------------

``bulk_create()`` supports ``ignore_conflicts`` through ``INSERT IGNORE``
and ``update_conflicts`` through ``ON DUPLICATE KEY UPDATE``. Unlike
PostgreSQL conflict targets, MySQL's duplicate-key update may trigger for
**any** conflicting unique constraint, not only the provided
``unique_fields``. ``INSERT IGNORE`` may also suppress warnings/errors
beyond duplicate keys. Treat both options as database-specific behavior.

MySQL 8 does not provide PostgreSQL-style ``RETURNING`` for supported bulk
conflict operations. The package uses a flush/refresh fallback for supported
plain inserts and rejects unsupported conflict+``returning`` and
UPDATE+``returning`` combinations rather than sending invalid SQL.

Affected-row counts for upserts are not identical to "number of logical
objects inserted"; database/client flags determine whether updates count
as one or two affected rows. Inspect the database state when business logic
needs to know which rows were created or changed.

Query compatibility
-------------------

Standard JSON path extraction is supported through SQLAlchemy's MySQL JSON
operators. PostgreSQL-only JSONB ``@>`` / ``?`` and ARRAY helpers are not
portable. MySQL versions and SQL modes affect ``INTERSECT``, ``EXCEPT``,
locking, uniqueness and case-sensitive string comparisons.

Live validation
---------------

.. code-block:: console

   export ASYNC_ORM_KIT_MYSQL_URL='mysql+asyncmy://user:password@localhost/orm_test?charset=utf8mb4'
   pytest -q tests/integration/test_mysql_async.py

Use only a disposable test database. SQL compilation and SQLite adapter tests
are not substitutes for native ``asyncmy`` execution. The CI job is configured
to fail if required provider suites skip.

Authentication
--------------

The MySQL extra includes ``cryptography`` because asyncmy requires it for
MySQL 8.4's default ``caching_sha2_password`` authentication over connections
that need RSA password exchange. Configure TLS and certificate verification
for remote deployments. The package does not weaken server authentication.
