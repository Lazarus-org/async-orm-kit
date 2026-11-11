Overview and design principles
==============================

Async ORM Kit is a **query layer**, not a replacement database engine. It
combines SQLAlchemy's mapped models, column types, relationships, dialects and
``AsyncSession`` with a compact asynchronous querying API:

.. code-block:: python

   users = await (
       User.objects.filter(session, active=True)
       .exclude(email__isnull=True)
       .order_by("-created_at", "id")
       .limit(20)
   )

The core system includes:

* SQLAlchemy declarative ``Model`` subclasses with automatic ``objects`` managers;
* immutable query chaining, field lookups and relationship traversal;
* ``Q`` boolean trees, ``F`` arithmetic, conditional expressions and aggregates;
* eager loading, subqueries, set operations, CTEs and window functions;
* bulk insert/update/delete helpers and explicit transaction ownership;
* configurable query planning and opt-in diagnostics;
* optional FastAPI, Alembic and storage adapters.

Principles
----------

**Explicit sessions.** Manager methods take a caller-provided ``AsyncSession``.
``Manager.using(session)`` provides optional scoped shorthand. No hidden global
session is created by this library.

**Explicit transactions.** Write methods flush or execute SQL; they do not
commit or roll back. The application controls the unit of work. Storage object
operations are also separate from database transactions.

**SQLAlchemy remains available.** Keep using native SQLAlchemy mapping,
``select()``, column expressions, constraints and ``session.execute()`` where
that is more suitable. ``QuerySet.cte()`` and ``CompoundQuerySet.subquery()``
return SQLAlchemy composable objects.

**Database semantics matter.** JSONB, PostgreSQL ARRAY and some row-lock flags
are dialect-specific. Unsupported operations either raise explicitly or are
listed as unverified; cross-dialect SQL compilation is not the same as running
against a real server.

**An opt-in package.** Only SQLAlchemy is a mandatory dependency. Database
drivers and integrations are installed as extras.

Non-goals
---------

Async ORM Kit does **not** provision or migrate databases, own connection pools,
manage background tasks, implement an automatic retry policy, provide an
automatic transaction-spanning file-store, or guarantee one ``AsyncSession``
can be shared across concurrent tasks.
