# Advanced SQL — JSON, ARRAY, set operations and CTEs

Advanced SQL support in `async-orm-kit` is built on SQLAlchemy's expression
compiler. It does **not** interpolate JSON paths, SQL identifiers, or values into
SQL strings. These operations belong to the application's existing explicit
`AsyncSession` and transaction lifecycle.

The examples assume `User` and `Event` are declarative models derived from
`async_orm_kit.Model`, with `User.name` and `Event.metadata_json` columns.

## JSON path extraction: SQLite, PostgreSQL and MySQL

Use `JSONPath(F("column"), *path, as_type=...)` to select and filter values inside
a mapped SQLAlchemy `JSON` or PostgreSQL `JSONB` column. The first argument is
an `F` reference (or a typed SQLAlchemy column), not a raw SQL field name.

```python
from async_orm_kit import F, JSONPath

async with SessionFactory() as session:
    queryset = (
        Event.objects.annotate(
            session,
            city=JSONPath(F("metadata_json"), "address", "city", as_type="string"),
            score=JSONPath(F("metadata_json"), "score", as_type="integer"),
        )
        .filter(city="Paris", score__gte=10)
        .values("id", "city", "score")
    )
    events = await queryset
```

Use `as_type="json"` (default) for an object or array; use `"string"`,
`"integer"`, `"float"` or `"boolean"` for scalar comparisons. Numeric JSON
array indexes may be supplied as path segments, e.g.
`JSONPath(F("metadata_json"), "tags", 0, as_type="string")`.

JSON null and SQL NULL may behave differently. Use explicit tests for your
application's expected handling of missing paths and null values on each
supported database; one dialect's behavior is not automatically portable to
another. The library validates the mapped column type and rejects malformed
paths and unsupported result types.

You may also filter directly using an expression predicate:

```python
Event.objects.filter(
    session,
    JSONPath(F("metadata_json"), "score", as_type="integer").eq(10),
)
```

## PostgreSQL-only JSONB operators

`JSONContains` is the PostgreSQL JSONB `@>` containment operator.
`JSONHasKey` is PostgreSQL JSONB `?` (top-level key existence). Both require a
mapped **JSONB** column. They fail SQL compilation for SQLite/MySQL rather
than returning incorrect results through a fallback `LIKE` comparison.

```python
from async_orm_kit import JSONContains, JSONHasKey

matches = await Event.objects.filter(
    session,
    JSONContains(F("metadata_json"), {"active": True}),
    JSONHasKey(F("metadata_json"), "owner"),
)
```

`JSONContains` and `JSONHasKey` require an actual PostgreSQL JSONB-mapped
column, not the generic SQLAlchemy `JSON` type. They can be used as annotated
boolean expressions too.

## PostgreSQL ARRAY operators

Use these expressions with a mapped `sqlalchemy.dialects.postgresql.ARRAY`:

```python
from async_orm_kit import ArrayContains, ArrayIndex, ArrayLength, ArrayOverlap

matches = await Event.objects.filter(
    session,
    ArrayContains(F("tags"), ["python"]),
    ArrayOverlap(F("tags"), ["sql", "database"]),
)

projected = await Event.objects.annotate(
    session,
    tag_count=ArrayLength(F("tags")),
    first_tag=ArrayIndex(F("tags"), 1),
).values("id", "first_tag", "tag_count")
```

PostgreSQL arrays are normally indexed from **1**. When the SQLAlchemy ARRAY
column is configured with `zero_indexes=True`, SQLAlchemy applies that column's
zero-based Python convention. A zero index on a standard one-based ARRAY is
rejected. ARRAY expressions fail SQL compilation outside PostgreSQL.

## SQL set operations

Combine **compatible QuerySets bound to the same session**:

```python
admins = User.objects.filter(session, role="admin").values_list("email", flat=True)
moderators = User.objects.filter(session, role="moderator").values_list("email", flat=True)

all_members = await admins.union(moderators).order_by("email")
with_duplicates = await admins.union(moderators, all=True)
both_roles = await admins.intersection(moderators)
admins_only = await admins.difference(moderators)
```

- `union()` uses SQL `UNION` (distinct); `union(all=True)` uses `UNION ALL`.
- `intersection()` uses SQL `INTERSECT` and `difference()` uses SQL `EXCEPT`.
- The result is a lazy `CompoundQuerySet` with `await`, `async for`, `stream()`,
  `first()`, `count()`, `exists()`, `order_by()`, `limit()`, `offset()`, `sql()`,
  `cte()` and `subquery()`.
- Branches must query the same mapped model, use the identical `AsyncSession`
  instance and have matching output projections. If a branch has ORM eager
  loading, DISTINCT ON, row locking, ordering or pagination, the operation is
  rejected. Apply ordering and pagination to the combined result instead.
- Full-model QuerySet combinations return ORM model instances through
  SQLAlchemy identity-map hydration. Model branches with annotations or
  deferred fields must use explicit projections instead.
- Ordering can reference only selected column names and is safely quoted;
  arbitrary SQL strings are not accepted.
- `count()` counts rows from the combined result, including `limit`/`offset`.
- Set operations are implemented with SQLAlchemy Core's SQL constructs. Database
  and server-version restrictions still apply (notably older MySQL versions
  lacking `INTERSECT`/`EXCEPT`).
- Set results cannot be further filtered with the regular Model QuerySet API.
  Use `.subquery()` or `.cte()` and SQLAlchemy `select()` for post-combination
  filtering or joining.

## Common table expressions

`QuerySet.cte(name=None, recursive=False)` yields a **SQLAlchemy CTE**. You can
pass it to native SQLAlchemy `select()` statements or compose a recursive term
using `cte.union_all()`:

```python
from sqlalchemy import select

seed = User.objects.filter(session, id=1).values_list("id", flat=True)
nodes = seed.cte("nodes", recursive=True)
nodes = nodes.union_all(
    select(User.id).join(nodes, User.manager_id == nodes.c.id)
)

rows = (await session.execute(select(nodes.c.id))).scalars().all()
```

Be deliberate with termination conditions for recursive CTEs. A cycle in the
underlying graph can cause unbounded recursion. SQLAlchemy and the database own
CTE behavior; Async ORM Kit does not run schema migrations or generate ad hoc
SQL strings.

## Compatibility and verification

SQLite execution and cross-dialect compilation are tested locally against
SQLAlchemy. Additional tests in `tests/integration` require actual native async
SQLite (`aiosqlite`), PostgreSQL (`asyncpg`) and MySQL (`asyncmy`) drivers. A
skipped module is **not** a passing integration test. Run the full database
contracts in CI before claiming production compatibility.

SQL set operations, recursive CTEs and JSON paths are not promised to have
identical semantics across database dialects and server versions. Prefer
portable JSON scalar extraction where possible and gate PostgreSQL-only ARRAY
and JSONB operators to a PostgreSQL-backed application.
