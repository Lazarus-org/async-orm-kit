# SQLAlchemy AsyncSession integration

Async ORM Kit uses SQLAlchemy 2.x `AsyncSession` directly. It does not create an
engine, session factory, connection pool, or transaction boundary. The application
owns those resources and passes an active session to each manager operation.

## Install an async driver

Install the driver for the database used by the application:

```bash
pip install "async-orm-kit[sqlite]"
# PostgreSQL
pip install "async-orm-kit[postgres]"
# MySQL
pip install "async-orm-kit[mysql]"
```

The extras install the corresponding SQLAlchemy async driver. They do not start or
configure a database server.

## Create the engine and session factory

```python
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

engine = create_async_engine(
    "sqlite+aiosqlite:///./app.db",
    pool_pre_ping=True,
)
SessionFactory = async_sessionmaker(
    engine,
    expire_on_commit=False,
)
```

Create the schema through SQLAlchemy metadata or the application's migration
workflow. Async ORM Kit does not create tables automatically.

```python
from async_orm_kit import Model
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

class User(Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
```

For a small development database, schema creation can be performed with
`AsyncConnection.run_sync()`:

```python
async with engine.begin() as connection:
    await connection.run_sync(Model.metadata.create_all)
```

Production applications should normally use Alembic migrations instead of
`metadata.create_all()`.

## Execute queries

Pass the active session as the first argument to manager methods:

```python
async def find_users():
    async with SessionFactory() as session:
        return await User.objects.filter(
            session,
            email__icontains="@example.com",
        ).order_by("email")
```

For repeated operations within one scope, `using()` binds the session to a manager
proxy:

```python
async def find_user(email: str):
    async with SessionFactory() as session:
        return await User.objects.using(session).filter(email=email).first()
```

A QuerySet retains the session supplied when it was created. Do not execute it after
that session has been closed.

## Transaction ownership

QuerySet and model mutation methods flush or execute statements in the current
transaction. They do not commit or roll back automatically.

```python
async def register_user(email: str):
    async with SessionFactory() as session:
        async with session.begin():
            user = await User.objects.create(session, email=email)
            # Additional writes in this block share the same transaction.
            return user
```

If a database operation fails and the transaction is not managed by a context
manager, the application must roll back the session before reusing it:

```python
async with SessionFactory() as session:
    try:
        await User.objects.create(session, email="person@example.com")
        await session.commit()
    except Exception:
        await session.rollback()
        raise
```

The ORM deliberately does not call `commit()` or `rollback()` inside model or
QuerySet methods. This allows callers to compose multiple operations atomically.

## Session concurrency

A SQLAlchemy `AsyncSession` represents mutable transaction and identity-map state.
It is **not safe to use one `AsyncSession` concurrently from multiple asyncio tasks**.

Use a separate session per concurrent task:

```python
import asyncio

async def process_one(user_id: int):
    async with SessionFactory() as session:
        return await User.objects.get(session, id=user_id)

results = await asyncio.gather(
    process_one(1),
    process_one(2),
)
```

Do not share one session across `asyncio.gather()`, background tasks, or unrelated
requests. An engine and session factory can be shared; individual sessions should
be scoped to one request or unit of work.

## Stream large result sets

Use `stream()` or direct async iteration to avoid loading the entire result set:

```python
async with SessionFactory() as session:
    query = User.objects.filter(session, email__icontains="@example.com")

    async for user in query.stream():
        await process_user(user)
```

A stream holds a database cursor and connection while it is being consumed. Keep
the session open for the full iteration. If iteration may stop early, explicitly
close the async iterator:

```python
async with SessionFactory() as session:
    stream = User.objects.all(session).stream()
    try:
        first_user = await anext(stream)
        await process_user(first_user)
    finally:
        await stream.aclose()
```

Async ORM Kit closes the underlying SQLAlchemy streaming result when the iterator
finishes, is closed, or is cancelled. Explicitly closing an iterator remains good
practice when consuming only part of a result.

## Async integration tests

The repository's SQLite integration suite uses a real SQLAlchemy `AsyncSession`
and the `aiosqlite` driver. Install the test dependencies and run:

```bash
uv sync
uv run pytest tests/integration -q
```

A skipped integration module means its optional database driver is not installed;
it is not evidence that the corresponding integration passed. PostgreSQL and MySQL
support should only be considered verified after their own live-driver integration
jobs pass.

Dispose the engine during application shutdown:

```python
await engine.dispose()
```
