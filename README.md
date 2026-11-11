# Async ORM Kit

[![PyPI version](https://img.shields.io/pypi/v/async-orm-kit?style=flat-square&logo=pypi&logoColor=white)](https://pypi.org/project/async-orm-kit/)
[![Python versions](https://img.shields.io/pypi/pyversions/async-orm-kit?style=flat-square&logo=python&logoColor=white)](https://pypi.org/project/async-orm-kit/)
[![License](https://img.shields.io/github/license/Lazarus-org/async-orm-kit?style=flat-square&color=blue)](LICENSE)

[![CI](https://github.com/Lazarus-org/async-orm-kit/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Lazarus-org/async-orm-kit/actions/workflows/ci.yml)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-FAB040?style=flat-square&logo=precommit&logoColor=white)](.pre-commit-config.yaml)
[![Ruff](https://img.shields.io/badge/lint_%26_format-Ruff-D7FF64?style=flat-square&logo=ruff&logoColor=white)](https://docs.astral.sh/ruff/)
[![codecov](https://codecov.io/gh/lazarus-org/async-orm-kit/branch/main/graph/badge.svg)](https://codecov.io/gh/lazarus-org/async-orm-kit)
[![Typed](https://img.shields.io/badge/typing-py.typed-3776AB?style=flat-square)](src/async_orm_kit/py.typed)

[![API documentation](https://img.shields.io/badge/docs-API_reference-0078D4?style=flat-square&logo=readthedocs&logoColor=white)](docs/reference/index.rst)
[![Last commit](https://img.shields.io/github/last-commit/Lazarus-org/async-orm-kit?style=flat-square)](https://github.com/Lazarus-org/async-orm-kit/commits/main/)
[![Open issues](https://img.shields.io/github/issues/Lazarus-org/async-orm-kit?style=flat-square)](https://github.com/Lazarus-org/async-orm-kit/issues)

[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.x-D71F00?style=flat-square)](https://www.sqlalchemy.org/)

**Django-inspired models, managers, expressions and QuerySets — powered by SQLAlchemy's async infrastructure.**

`async-orm-kit` gives you an expressive, chainable ORM query API while preserving SQLAlchemy 2.x mappings, drivers, sessions, transactions and composable SQL expressions. It's an **async-first library** for applications that want Django-style querying without adopting Django or losing SQLAlchemy control.

```python
users = await (
    User.objects.filter(session, active=True, age__gte=18)
    .exclude(email__isnull=True)
    .order_by("-created_at", "id")
    .limit(20)
)
```

## Features

| Area | Capabilities |
| --- | --- |
| **Models & managers** | SQLAlchemy declarative models, default/custom managers, model validation, serialization helpers, model-specific exceptions |
| **QuerySets** | Lazy immutable chaining, `filter`, `exclude`, `get`, `first`, `count`, `exists`, projections, ordering, pagination, async streaming |
| **Lookups & logic** | `Q` AND/OR/NOT, field and relationship traversal, NULL handling, nested JSON path lookups |
| **Expressions** | `F`, `Value`, `Case`/`When`, arithmetic and atomic SQL updates, typed SQL functions |
| **Aggregations** | `Count`, `Sum`, `Avg`, `Min`, `Max`, grouped annotations, `HAVING`, filtered and custom aggregates |
| **Advanced SQL** | `Subquery`, nested `OuterRef`, `Exists`, window functions, `UNION` / `INTERSECT` / `EXCEPT`, CTEs and recursive CTEs |
| **Relationships** | `select_related`, `prefetch_related`, relationship planning, correlated collection exclusions, related-row counts |
| **Database writes** | Create/update/delete, bulk operations, conflict handling, generated defaults, transaction-aware lifecycle hooks |
| **PostgreSQL** | JSONB and ARRAY operators, row-lock options and dialect-specific upserts |
| **Extensibility** | QuerySet-local custom lookups, trusted SQL function/aggregate factories, deferred fields, keyset pagination |
| **Observability** | Event-backed `QueryProfiler`, SQL timings, task-scoped captures, bounded history, heuristic N+1 detection |
| **Integrations** | Optional `async-storage-kit` / `FileField`, Alembic and FastAPI adapters |

The design is intentionally **session-explicit, transaction-neutral and SQLAlchemy-native**. QuerySet methods don't hide network connections, migrations, commits or rollbacks.

## Installation

**Requirements:** CPython 3.11–3.15, SQLAlchemy 2.x and a compatible async database driver. Only SQLAlchemy is a mandatory runtime dependency.

```bash
# Install the source checkout with SQLite support
python -m pip install -e ".[sqlite]"

# Distribution extras, when a published build is available:
pip install "async-orm-kit[postgres]"   # asyncpg
pip install "async-orm-kit[mysql]"      # asyncmy
pip install "async-orm-kit[storage]"    # async-storage-kit
pip install "async-orm-kit[migrations]" # Alembic
pip install "async-orm-kit[fastapi]"    # FastAPI
```

SQLite uses `aiosqlite`; PostgreSQL uses `asyncpg`; MySQL uses `asyncmy`. The package does not start a database or configure a connection pool for you. See [Installation](docs/installation.rst) and the [database compatibility matrix](docs/compatibility.rst).

## Quick start

Define models with familiar SQLAlchemy mapping primitives:

```python
from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column, relationship

from async_orm_kit import Model

engine = create_async_engine("sqlite+aiosqlite:///./app.db")
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


class Author(Model):
    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    posts: Mapped[list["Post"]] = relationship(back_populates="author")


class Post(Model):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id"))
    title: Mapped[str] = mapped_column(String(255))
    view_count: Mapped[int] = mapped_column(default=0)
    author: Mapped[Author] = relationship(back_populates="posts")
```

In a disposable development database you can create tables through SQLAlchemy. For deployed applications, use reviewed Alembic migrations:

```python
async with engine.begin() as connection:
    await connection.run_sync(Model.metadata.create_all)
```

Write through the caller-owned transaction:

```python
async with SessionFactory() as session:
    async with session.begin():
        author = await Author.objects.create(session, name="Ada")
        await Post.objects.create(session, author_id=author.id, title="Async SQL")
```

Query with eager loading and normal `AsyncSession` ownership:

```python
async with SessionFactory() as session:
    authors = await (
        Author.objects.filter(session, active=True)
        .prefetch_related("posts")
        .order_by("name")
    )

    for author in authors:
        print(author.name, len(author.posts))
```

**Important:** Manager methods accept `session` as their **first argument**. A QuerySet remembers that session; chaining methods such as `.filter(...)` and `.order_by(...)` do **not** take it again. Alternatively, `Author.objects.using(session)` returns a session-bound manager proxy.

## Querying and expressions

### Boolean logic, aggregation and atomic updates

```python
from async_orm_kit import Count, F, Q, Sum

# Boolean predicates and relationship traversal
query = Author.objects.filter(
    session,
    Q(name__icontains="ada") | Q(posts__title__icontains="sql"),
)

# Count related rows, preserving parents with no related records
ranked = await (
    Author.objects.annotate(session, post_count=Count("posts"))
    .filter(post_count__gte=1)
    .order_by("-post_count")
)

# Updates use SQL-side expressions and still require an application transaction.
async with SessionFactory() as write_session:
    async with write_session.begin():
        await Post.objects.filter(write_session, id=42).update(
            view_count=F("view_count") + 1
        )
```

Use `values(...).annotate(...)` for grouped summaries and `aggregate(...)` for a terminal summary dictionary. Complex combinations of aggregate annotations and row predicates support `Q` OR/AND/NOT using correlated existence semantics. See [Aggregations](docs/advanced/aggregation.rst) and [Expressions](docs/advanced/expressions.rst).

### Subqueries, set operations and JSON

```python
from async_orm_kit import Exists, OuterRef

matching = Post.objects.filter(
    session, author_id=OuterRef("id"), title__icontains="async"
)
authors = await Author.objects.filter(session, Exists(matching))

# Set operations on compatible projections and the same session
first = Author.objects.filter(session, active=True).values("id", "name")
second = Author.objects.filter(session, name__startswith="A").values("id", "name")
selected = await first.union(second).filter(name__icontains="a").order_by("id")

# For a model with a mapped SQLAlchemy JSON column named payload:
# events = await Event.objects.filter(session, payload__profile__age__gte=18)
```

The package also supports typed `JSONPath`, PostgreSQL JSONB/ARRAY expressions, CTEs, window functions and SQLAlchemy-composable subqueries. Dialect-specific operators are not silently emulated with text matching. See [Advanced SQL](docs/advanced/sets-cte.rst), [JSON & ARRAY](docs/advanced/json-array.rst) and [Correlated subqueries](docs/advanced/subqueries.rst).

### Pagination and streaming

```python
base = Author.objects.order_by(session, "id")
first_page = await base.limit(25)

if first_page:
    next_page = await base.seek_after(first_page[-1].id).limit(25)

async for author in base.stream():
    process(author)
```

Keyset pagination requires a deterministic non-nullable ordering that includes the primary key. It accepts **cursor values**, not an opaque signed token. Keep the session open throughout streaming. See [Pagination](docs/advanced/pagination.rst).

## Transactions and integrations

### You own the session and transaction

```python
async with SessionFactory() as session:
    async with session.begin():
        await Author.objects.create(session, name="Grace")
```

Model and QuerySet mutation methods flush or execute SQL **without committing or rolling back**. A failed flush requires rollback before session reuse. Sessions must not be shared across concurrent tasks. Model `post_save` and `post_delete` observers run after flush, **before commit**; they aren't after-commit hooks.

### Optional framework and storage integrations

- **[FastAPI](docs/integrations-rst/fastapi.rst):** request-scoped `AsyncSession` dependency, explicit application transactions and safe exception responses.
- **[Alembic](docs/integrations-rst/alembic.rst):** import model metadata for autogeneration; schema migrations remain application-owned.
- **[FileField / async-storage-kit](docs/integrations-rst/storage.rst):** asynchronous upload, streaming download, metadata and backend adapters. Object storage is **not** transactionally coupled to the database.
- **[Observability](docs/advanced/observability.rst):** SQLAlchemy-event query timing, bounded execution traces and suspected N+1 patterns.

## Documentation

The primary documentation is authored in **reStructuredText** and built with Sphinx. Start at [`docs/index.rst`](docs/index.rst), or open the sections directly:

| Getting started | ORM fundamentals | Advanced queries | Operations |
| --- | --- | --- | --- |
| [Install](docs/installation.rst) | [Models](docs/guide/models.rst) | [Expressions](docs/advanced/expressions.rst) | [Compatibility](docs/compatibility.rst) |
| [Quick start](docs/quickstart.rst) | [Managers](docs/guide/managers.rst) | [Aggregations](docs/advanced/aggregation.rst) | [Troubleshooting](docs/guide/troubleshooting.rst) |
| [Design](docs/introduction.rst) | [QuerySets](docs/guide/querysets.rst) | [Subqueries](docs/advanced/subqueries.rst) | [Development](docs/maintainers/development.rst) |
| [Sessions](docs/guide/sessions.rst) | [Relationships](docs/guide/relationships.rst) | [Set operations & CTEs](docs/advanced/sets-cte.rst) | [Release policy](docs/maintainers/release.rst) |

The [complete API reference](docs/reference/index.rst) documents exported classes, methods, functions, exceptions, integrations and custom expression types. Supporting architecture decisions are in `docs/architecture/ADR/`.

### Build locally

```bash
python -m pip install -e ".[docs]"
sphinx-build -b html -W --keep-going docs docs/_build/html
```

The repository also supplies `.readthedocs.yaml` for hosted Sphinx builds.

## Development and quality

```bash
uv lock --check
uv sync --locked
uv run pytest -q
uv run ruff check src tests .github/scripts
uv run ruff format --check src tests .github/scripts
uv run mypy src/async_orm_kit
uv run bandit -c pyproject.toml -r src/async_orm_kit
```

The source includes offline SQL correctness tests and native-driver integration suites for `aiosqlite`, `asyncpg` and `asyncmy`. A skipped provider test is **not** passing compatibility evidence. Before the first stable release, the supported Python/OS matrix, live database tests, security/type checks, distributions and documentation build must all be verified. See [Release readiness](docs/maintainers/release.rst).

## Contributing, security and licensing

Contributions and community conduct are governed by the existing detailed [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) documents. Report security issues using [SECURITY.md](SECURITY.md), rather than public issues. Release notes are in [CHANGELOG.md](CHANGELOG.md).

Async ORM Kit is licensed under the [MIT License](LICENSE).
