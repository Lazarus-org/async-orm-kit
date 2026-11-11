Quick start
===========

This example uses SQLAlchemy 2.x mappings and the ORM's real
``Manager(session, ...)`` contract. SQLite is a convenient development driver;
production schema changes should use reviewed migrations.

Define a model and session factory
----------------------------------

.. code-block:: python

   from sqlalchemy import Boolean, String
   from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
   from sqlalchemy.orm import Mapped, mapped_column

   from async_orm_kit import Model

   engine = create_async_engine("sqlite+aiosqlite:///./example.db")
   SessionFactory = async_sessionmaker(engine, expire_on_commit=False)

   class User(Model):
       __tablename__ = "users"

       id: Mapped[int] = mapped_column(primary_key=True)
       email: Mapped[str] = mapped_column(String(255), unique=True)
       active: Mapped[bool] = mapped_column(Boolean, default=True)
       login_count: Mapped[int] = mapped_column(default=0)

Create the schema for local experimentation
-------------------------------------------

.. code-block:: python

   async def create_dev_schema():
       async with engine.begin() as connection:
           await connection.run_sync(Model.metadata.create_all)

``create_all`` is not a migration system. Use :doc:`integrations-rst/alembic`
when a schema must be upgraded over time.

Write data in a transaction
---------------------------

.. code-block:: python

   async def add_user(email: str) -> User:
       async with SessionFactory() as session:
           async with session.begin():
               user = await User.objects.create(session, email=email)
               return user

``create()`` flushes, but the ``session.begin()`` context is what commits on
success or rolls back on failure.

Read and filter
---------------

.. code-block:: python

   async def list_active_users() -> list[User]:
       async with SessionFactory() as session:
           return await (
               User.objects.filter(session, active=True)
               .exclude(email__isnull=True)
               .order_by("email")
               .limit(25)
           )

   async def get_one(email: str) -> User | None:
       async with SessionFactory() as session:
           return await User.objects.filter(session, email=email).first()

Alternatively, bind the session explicitly once:

.. code-block:: python

   async with SessionFactory() as session:
       users = await User.objects.using(session).filter(active=True)

Update without fetching
-----------------------

.. code-block:: python

   from async_orm_kit import F

   async with SessionFactory() as session:
       async with session.begin():
           await User.objects.filter(session, id=1).update(
               login_count=F("login_count") + 1
           )

Atomic ``F`` updates are resolved into SQL expressions rather than evaluating
Python-side values.

Continue reading
----------------

* :doc:`guide/sessions` for transactions and failure handling.
* :doc:`guide/querysets` for querying and pagination.
* :doc:`guide/relationships` for joins and eager loading.
* :doc:`advanced/expressions` for conditional SQL expressions.
