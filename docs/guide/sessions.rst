Sessions, transactions and concurrency
======================================

**This is the central contract of Async ORM Kit:** your application owns the
SQLAlchemy ``AsyncEngine``, ``async_sessionmaker``, ``AsyncSession`` and
transaction boundaries.

.. code-block:: python

   from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

   engine = create_async_engine(
       "postgresql+asyncpg://user:password@localhost/app",
       pool_pre_ping=True,
   )
   SessionFactory = async_sessionmaker(engine, expire_on_commit=False)

Write within a defined unit of work
-----------------------------------

.. code-block:: python

   async with SessionFactory() as session:
       async with session.begin():
           user = await User.objects.create(session, email="a@example.com")
           # Other SQLAlchemy or ORM writes share this transaction.

``save()``, ``delete()``, ``create()``, ``bulk_create()``, ``bulk_update()``,
``update()`` and ``delete()`` **never** call ``commit()`` or ``rollback()``
for you. A flush can write SQL before commit but still be rolled back.

Failure handling
----------------

If not using a transaction context manager, roll back after a failed flush
before reusing a session:

.. code-block:: python

   async with SessionFactory() as session:
       try:
           await User.objects.create(session, email="a@example.com")
           await session.commit()
       except Exception:
           await session.rollback()
           raise

Sessions are not concurrency-safe
---------------------------------

Do not share one ``AsyncSession`` between parallel ``asyncio`` tasks or
unrelated requests. Give each task/request its own session. ``QuerySet``
retains the session it was created with, and streaming requires that the
session stay open through the entire iteration.

Nested transactions
-------------------

Use SQLAlchemy ``session.begin_nested()`` when savepoints are appropriate:

.. code-block:: python

   async with SessionFactory() as session:
       async with session.begin():
           async with session.begin_nested():
               await User.objects.create(session, email="nested@example.com")

Savepoints and isolation levels are database-specific. This library does
not add a hidden retry manager or automatically escalate isolation.

Observers and external side effects
-----------------------------------

``post_save`` and ``post_delete`` hooks happen **after flush but before
commit**. Never assume they mean a transaction has committed. Use an
application-managed outbox or after-commit mechanism for irreversible
external actions, such as email, payments or storage deletion.

See :doc:`../integrations-rst/fastapi` for request-scoped sessions.
