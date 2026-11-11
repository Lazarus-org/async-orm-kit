Troubleshooting
===============

``No database session was provided``
------------------------------------

Manager methods need an ``AsyncSession`` as their first argument, unless
using ``Manager.using(session)``. Keep the session alive through QuerySet
execution and streaming.

``MissingGreenlet`` on a mapped field
-------------------------------------

Implicit SQLAlchemy lazy loading of a deferred field or relationship can
trigger ``MissingGreenlet`` in async code. Prefetch needed relations or use
``await session.refresh(obj, ["field_name"])`` for deferred scalar fields.

``InvalidRequestError: A transaction is already begun``
-------------------------------------------------------

SQLAlchemy may start a transaction automatically at the first read. Start
``session.begin()`` before the unit of work, or commit/rollback a previous
transaction intentionally. QuerySet write helpers do not commit automatically.

Wrong COUNT results
-------------------

Ensure related fields and join paths are correct. Use
``Count("related_items", distinct=True)`` when joining other to-many paths
would multiply related rows. Review generated SQL and test zero-related-row
cases.

Unsupported JSON/ARRAY operators
--------------------------------

PostgreSQL JSONB operators require a mapped ``JSONB`` column and PostgreSQL
server. ARRAY helpers require PostgreSQL ``ARRAY``. Standard SQLite/MySQL
JSON path operations use different SQL semantics.

Keyset pagination rejects a query
---------------------------------

Check that ``order_by`` includes all nonnullable root primary-key fields,
contains no relationship/annotation fields, and that the QuerySet has no
OFFSET, DISTINCT, grouping or row locks.

Set-operation errors
--------------------

All branches need identical session and compatible projection types/names.
After a UNION/INTERSECT/EXCEPT, filter only projected columns and apply
``filter``/``exclude`` *before* compound ordering/pagination.

Tests skipped in CI
-------------------

Install the target driver and provision the disposable test database or
storage backend. A skipped integration module is not successful validation.
The provider jobs are designed to fail when required suites skip.

Docs build fails with missing Sphinx/theme
------------------------------------------

Install the ``docs`` extra and run ``sphinx-build`` from the repository root.
The Read the Docs configuration points to ``docs/conf.py`` and builds the
RST site rooted at ``docs/index.rst``.
