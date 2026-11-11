# ORM extensibility and keyset pagination

The package provides **opt-in**, SQLAlchemy-based extension points, explicit deferred
columns, and deterministic cursor predicates. They do not introduce mandatory
runtime dependencies, automatic transaction management, or global lookup mutation.

## Register a custom lookup

```python
from async_orm_kit import Model

# Handler arguments are a SQLAlchemy column and the supplied lookup value.
def multiple_of(column, divisor):
    return column % divisor == 0

async with SessionFactory() as session:
    query = (
        Item.objects.all(session)
        .with_lookup("multiple_of", multiple_of)
        .filter(score__multiple_of=5)
        .order_by("id")
    )
    rows = await query
```

The handler must return a SQLAlchemy **Boolean-typed** `ColumnElement`. Names
must be lowercase identifiers and cannot override built-in lookup names.
Registrations are isolated on each QuerySet clone, not stored in a global or
model-wide mutable registry. `with_lookups(foo=foo_handler, bar=bar_handler)`
adds several at once. Custom lookups also work within `Q(...)` predicates.

**Security:** Register only application-owned handlers; they execute Python
code and can construct arbitrary SQLAlchemy expressions. Use SQLAlchemy
operator methods, bound operands and `literal()`—not textual SQL assembled
from untrusted values. The factory cannot be registered by API clients.

## Create a typed custom SQL function

```python
from sqlalchemy import func
from async_orm_kit import F, Function

query = Item.objects.annotate(
    session,
    prefix=Function(lambda name: func.substr(name, 1, 3), F("name")),
).values("id", "prefix")
results = await query
```

`Function(factory, *operands)` resolves `F`, `Value`, arithmetic and other
supported ORM expressions, preserving SQLAlchemy bind parameters. The factory
must return a SQLAlchemy `ColumnElement`; raw SQL strings are not accepted as
functions or operands. Factories are trusted application source code.

## Define a custom aggregate

```python
from sqlalchemy import func
from async_orm_kit import AggregateFunction

query = (
    Item.objects.values(session, "category")
    .annotate(total=AggregateFunction("price", lambda col: func.sum(col)))
    .order_by("category")
)
results = await query
```

`AggregateFunction(field, factory, distinct=False, filter=..., default=...,
output_field=...)` reuses the existing field resolver, conditional aggregation,
DISTINCT and output-type pipeline. It marks the resulting SQL expression as an
aggregate so nested arithmetic receives the correct `GROUP BY` treatment—even
for custom aggregate functions not named in the built-in SQL analyzer.

**Dialect compatibility is the caller's responsibility:** nonstandard SQL
functions may compile or execute on only one database. Configure appropriate
SQLite, PostgreSQL and MySQL integration tests for every custom factory.

## Defer large scalar columns

```python
async with SessionFactory() as session:
    record = await Item.objects.defer(session, "description").get(id=1)
    # Explicitly fetch the deferred column before reading it in async code:
    await session.refresh(record, ["description"])
    print(record.description)
```

`defer()` accepts only mapped scalar columns of the root model, not primary-key
columns or relationship paths. Calls can be chained to defer more columns.
`defer()` is incompatible with `only()`, `values()` and `values_list()`.

**Async warning:** Accessing a deferred value directly may issue implicit
lazy-loading I/O and raise SQLAlchemy's `MissingGreenlet`. Use
`await session.refresh(instance, ["column"])`, or use an application-specific
AsyncAttrs pattern if supported. This library does not schedule background
loading and does not implicitly commit or roll back transactions.

## Keyset/cursor pagination

```python
async with SessionFactory() as session:
    base = Item.objects.all(session).order_by("created_at", "id")
    first_page = await base.limit(20)

    if first_page:
        last = first_page[-1]
        next_page = await base.seek_after(last.created_at, last.id).limit(20)
```

`seek_after(*values)` and `seek_before(*values)` generate a strict lexicographic
predicate over the current `order_by()` columns. Mixed directions work:
`order_by("-created_at", "id")` uses a descending comparison for timestamp
and ascending comparison for primary key. Cursor values remain bound SQL
parameters, never interpolated identifiers.

The ordering must:

- Be nonempty and use **root-model scalar column names** (not expressions,
  relationship paths or annotation aliases).
- Include **all primary-key columns** as a stable tie-breaker.
- Use **non-nullable** mapped columns with non-NULL scalar cursor values.
- Avoid OFFSET, DISTINCT, grouping/aggregates, row locks and row-multiplying
  collection JOIN filters/ordering.

Only one seek cursor is allowed per QuerySet, and changing ordering or adding
OFFSET after seeking is rejected. The returned rows preserve the original sort
direction. `seek_before()` returns preceding rows in that same direction; callers
must decide how to present backward navigation. The API accepts decoded scalar
values, **not** a signed/opaque token. Applications handling public cursors
should validate, authenticate, serialize and encode their own cursor tokens.

Keyset pagination is not a snapshot: concurrent inserts and updates to ordering
columns may change which records appear between page requests. Use an
appropriate transaction isolation level or immutable sort keys where required.

## Verification

```bash
pytest -q tests/test_extensibility_pagination.py
pytest -q tests/integration/test_stage4_async.py
```

The second module requires `aiosqlite` and tests actual SQLAlchemy
`AsyncSession` execution. A skipped integration module does **not** demonstrate
that async behavior has passed.
