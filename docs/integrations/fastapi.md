# FastAPI integration

FastAPI is optional. Async ORM Kit provides a request-scoped `AsyncSession`
dependency and safe exception-handler defaults; it does not create the engine,
commit successful requests, or manage application shutdown automatically.

## Install the optional dependency

```bash
pip install "async-orm-kit[fastapi]"
```

## Create the engine and request dependency

```python
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from async_orm_kit.integrations.fastapi import (
    create_session_dependency,
    register_exception_handlers,
)

engine = create_async_engine(
    "postgresql+asyncpg://app:password@localhost/app",
    pool_pre_ping=True,
)
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
    from myapp.models import User

    users = await User.objects.filter(session).order_by("id")
    return [user.to_dict() for user in users]
```

The dependency creates a fresh session for each request, yields it, rolls back an
active transaction if request execution raises or is cancelled, and closes the
session on exit. It deliberately does **not** commit successful requests.

## Transaction ownership

Keep transaction boundaries in your application/service layer. Start the
transaction before the first database operation in that unit of work:

```python
@app.post("/users")
async def create_user(payload: UserCreate, session: AsyncSession = Depends(get_session)):
    from myapp.models import User

    async with session.begin():
        user = User(email=payload.email)
        await user.save(session)
        return user.to_dict()
```

A session may autobegin when the first query is executed. Do not call
`session.begin()` after a prior operation has already started a transaction; either
wrap the complete unit of work at its entry point or explicitly commit/rollback
according to your application's policy. ORM mutation methods never commit for you.

Do not share one `AsyncSession` across concurrent asyncio tasks. Use a separate
session per task/request. The engine and session factory may be application-scoped;
the yielded session is request-scoped.

## Exception handlers

`register_exception_handlers(app)` installs these generic, non-sensitive mappings:

| Async ORM Kit exception | HTTP status | Response detail |
|---|---:|---|
| `ObjectDoesNotExist` | 404 | `Resource not found` |
| `MultipleObjectsReturned` | 409 | `Multiple resources matched the request` |
| `DatabaseError` | 500 | `A database operation failed` |

Database messages, SQL statements, bound parameters, credentials, and original
causes are not included in responses. Log the exception safely in application
middleware or service boundaries if diagnostics are required. Query construction
errors are not globally mapped because they may indicate programming defects;
register application-specific handlers when client input is intentionally used
to construct filters.

## Storage and response serialization

`FileField` remains an independent integration. Inject an `AsyncStorageKitAdapter`
into the field and expose only the URL or metadata your API intends to make public.
Do not serialize private storage paths, provider credentials, or signed URLs into
logs or unrestricted API responses.

The integration tests use FastAPI's ASGI interface to verify dependency injection,
per-request session creation/closure, rollback on request failures, and safe
exception responses. They use a fake session factory and do not replace the live
SQLite/PostgreSQL/MySQL driver integration suites.
