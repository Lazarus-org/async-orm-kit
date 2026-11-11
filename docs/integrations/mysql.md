# MySQL 8.4 / asyncmy integration

`async-orm-kit` uses SQLAlchemy's native `mysql+asyncmy` dialect and accepts
application-managed `AsyncSession` instances. It does not create your database,
engine, session factory, pool, or transaction boundaries.

## Installation

```bash
pip install 'async-orm-kit[mysql]'
```

For repository development:

```bash
uv sync --extra test --extra mysql
```

## Database connection and transaction lifecycle

```python
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

engine = create_async_engine(
    "mysql+asyncmy://user:password@127.0.0.1:3306/my_app?charset=utf8mb4",
    pool_pre_ping=True,
)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)

async with SessionFactory() as session:
    async with session.begin():
        await User.objects.create(session, email="person@example.com")

await engine.dispose()
```

Use an independent session per request/task. Your application owns commits and
rollbacks. A flush failure requires rollback before session reuse. MySQL's default
transaction isolation and collation rules may differ from PostgreSQL/SQLite; make
them explicit at the application level when needed.

## MySQL-specific write semantics

| Operation | MySQL 8.4 behavior |
|---|---|
| `create()` | Flushes one instance; caller owns the transaction |
| `bulk_create(returning=False)` | Core executemany; reports DBAPI affected-row count; Python defaults applied independently |
| `bulk_create(returning=True)` without conflicts | ORM `add_all()`/`flush()`, followed by `refresh()` per instance to populate generated values; more round trips than PostgreSQL RETURNING |
| `bulk_create(update_conflicts=True, returning=False)` | `ON DUPLICATE KEY UPDATE` |
| `bulk_create(ignore_conflicts=True, returning=False)` | `INSERT IGNORE`; use cautiously |
| `bulk_create(returning=True)` with conflict mode | Explicit `NotImplementedError` before writes |
| `update(returning=True)` | Explicit `NotImplementedError` before writes |
| `update(returning=False)` | Standard SQLAlchemy UPDATE for local-column filters; caller owns transaction |
| `delete()` with observers | `SELECT ... FOR UPDATE` to capture IDs before DELETE for simple filters |

**Conflict targets:** MySQL's `ON DUPLICATE KEY UPDATE` acts on conflicts with
*any* primary key or unique index. Unlike PostgreSQL/SQLite `ON CONFLICT`, MySQL
cannot target the `unique_fields` list. That argument is required by the shared
API, but does not constrain MySQL's conflict behavior. Carefully review upserts
on tables with more than one unique constraint.

**INSERT IGNORE:** In MySQL it can turn errors besides uniqueness violations
into warnings and continue. Validate all input before using it, and avoid it
when strict failure semantics are required.

**Affected-row counts:** `rowcount` is the DBAPI's affected/matched-row count,
not necessarily the number of logical objects inserted or updated; upserts can
report different counts for inserted, updated, or unchanged rows.

**RETURNING:** MySQL 8.4 does not provide PostgreSQL-like INSERT/UPDATE/DELETE
RETURNING. Native MySQL operations requiring it are rejected or implemented
through the limited explicit fallback above. MariaDB has different features
and is not yet an independently verified target.

**Joined filters on bulk writes:** MySQL bulk UPDATE and DELETE currently reject
relationship-join filters, rather than issuing invalid or incomplete multi-table
statements. Query with local-column filters or load and explicitly mutate models
when the relationship constraint must be enforced.

**Observer deletes:** SELECT FOR UPDATE locks the selected rows until the
caller's transaction ends. Joined relationship filters with delete observers
are not supported in this fallback; concurrent changes to related tables are
not modeled. Observers are executed before commit, not after commit.

## Disposable live integration

Provision a fresh, disposable database named `async_orm_kit_test`. The tests
create and drop tables prefixed `phase5_mysql_`. **Do not point them at a
production database.**

```powershell
$env:ASYNC_ORM_KIT_MYSQL_URL = "mysql+asyncmy://orm_test:orm_test@127.0.0.1:3306/async_orm_kit_test?charset=utf8mb4"
uv sync --extra test --extra mysql
uv run pytest -q tests/integration/test_mysql_async.py
```

The fixture checks the `mysql+asyncmy` scheme and a database name ending in
`_test`. If a URL is configured but MySQL is unreachable, tests **fail**.
Only an absent URL results in a skipped integration module; CI requires it.

### Example Docker service (local only)

```powershell
docker run --name async-orm-kit-mysql `
  -e MYSQL_ROOT_PASSWORD=local_dev_root_only `
  -e MYSQL_DATABASE=async_orm_kit_test `
  -e MYSQL_USER=orm_test `
  -e MYSQL_PASSWORD=orm_test `
  -p 3306:3306 -d mysql:8.4
```

Avoid publishing these development-only passwords. The GitHub Actions job
provisions an independent ephemeral MySQL service with the same contract.

## Support status

The offline MySQL SQL compiler and method guards have passed, and the live
integration suite/CI job are implemented. Native `asyncmy` tests have **not run**
in the current development environment. A successful hosted/live execution is
required before MySQL is marked as a verified supported backend.
