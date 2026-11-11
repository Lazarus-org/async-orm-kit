FastAPI integration
===================

Install the optional ``fastapi`` extra, along with a database driver:

.. code-block:: console

   python -m pip install "async-orm-kit[fastapi,postgres]"

Request-scoped AsyncSession
---------------------------

.. code-block:: python

   from contextlib import asynccontextmanager
   from fastapi import Depends, FastAPI
   from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
   from async_orm_kit.integrations.fastapi import (
       create_session_dependency, register_exception_handlers,
   )

   engine = create_async_engine("postgresql+asyncpg://user:password@localhost/app")
   SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
   get_session = create_session_dependency(SessionFactory)

   @asynccontextmanager
   async def lifespan(app: FastAPI):
       yield
       await engine.dispose()

   app = FastAPI(lifespan=lifespan)
   register_exception_handlers(app)

   @app.get("/users")
   async def list_users(session: AsyncSession = Depends(get_session)):
       users = await User.objects.filter(session, active=True)
       return [user.to_dict() for user in users]

Use an application-importable ``User`` mapped model; the example assumes it
exists in the same module or has been imported.

Transaction ownership
---------------------

The dependency creates a fresh session per request and closes it on exit.
It rolls back an active transaction when endpoint processing fails or is
cancelled. It does **not** commit successful requests.

.. code-block:: python

   @app.post("/users")
   async def create_user(payload: UserCreate, session: AsyncSession = Depends(get_session)):
       async with session.begin():
           user = User(email=payload.email)
           await user.save(session)
           return user.to_dict()

``UserCreate`` is your application-defined validated input schema. Start the
transaction before the first query in its unit of work to avoid SQLAlchemy
``autobegin`` conflicts. Do not share sessions across concurrent requests or
tasks; the sessionmaker and engine can be app-scoped.

Default exception mapping
-------------------------

``register_exception_handlers(app)`` maps:

.. list-table:: Default HTTP mappings
   :header-rows: 1

   * - ORM exception
     - Status
     - Detail
   * - ``ObjectDoesNotExist``
     - 404
     - Resource not found
   * - ``MultipleObjectsReturned``
     - 409
     - Multiple resources matched the request
   * - ``DatabaseError``
     - 500
     - A database operation failed

Handlers intentionally exclude SQL, parameters, credentials and internal
exception causes from HTTP responses. These are defaults; applications
should define their own external API error contracts.
