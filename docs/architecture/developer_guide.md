> **Status: historical design notes, not a verified API reference.** These documents were extracted from the original application. Some examples and design descriptions may not match the current implementation. Use the package source and README as the source of truth; the guides will be reconciled in the documentation/release phase.

# Developer Guide

## 1. Quick Start

Get up and running in 60 seconds:

```python
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from async_orm_kit.models import Model
from async_orm_kit.manager import Manager

# Define a model
class Author(Model):
    __tablename__ = "authors"

    id = Column(Integer, primary_key=True)
    name = Column(String(100))

# Create session
engine = create_async_engine("sqlite+aiosqlite:///db.sqlite3")
session = AsyncSession(engine)

# Query
authors = await Author.objects.filter(name__icontains="john", session=session)

# Results
for author in authors:
    print(author.name)
```

**Key takeaways:**
- Models use SQLAlchemy `Column` definitions directly
- Sessions must be passed explicitly to all queries
- QuerySet API is Django-familiar: `filter()`, `exclude()`
- Everything is async (`await` required)

---

## 2. Architecture Overview

### Read Operations (fetch, count, exists)

```text
┌─────────────┐
│  User Code  │
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────────┐
│ QuerySet API            │
│ (filter, select_related, annotate)      │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ QueryBuilder                            │
│ (collects operations into QueryState)   │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ QueryState                              │
│ (filters, joins, annotations, ordering) │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ Filter Resolution Pipeline              │
│ (parses lookups → resolves fields)      │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ JoinGraph                               │
│ (registers and orders JOIN operations)  │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ QueryPlanner                            │
│ (optimizes: JOIN promotion, strategies) │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ QueryCompiler                           │
│ (assembles final SQLAlchemy SELECT)     │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ QueryExecutor                           │
│ (fetch_all, stream, count, get, first)  │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ SQLAlchemy Core                         │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ Database                                │
└─────────────────────────────────────────┘
```

### Write Operations

**Create:**
```text
┌─────────────┐
│  User Code  │
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────────┐
│ QuerySet.create()                       │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ QueryWriter                             │
│ (validate and execute)                  │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ Database                                │
└─────────────────────────────────────────┘
```

**Update/Delete (with filtering):**
```text
┌─────────────┐
│  User Code  │
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────────┐
│ QuerySet API (filter chain)             │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ QueryBuilder → QueryState               │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ Filter Resolution Pipeline              │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ JoinGraph                               │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ QueryPlanner                            │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ .update() / .delete() called            │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ QueryWriter                             │
│ (execute UPDATE/DELETE)                 │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ Database                                │
└─────────────────────────────────────────┘
```

**Key Insight:** Your QuerySet calls don't execute immediately. They build up a plan that gets optimized before hitting the database.

---

## 3. Core Concepts

### 3.1 Model & Column Definition

**Dependencies:** None (starting point)

**Purpose:** Define database tables using SQLAlchemy columns with Django-style model structure.

#### Basic Model

```python
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from async_orm_kit.models import Model
from async_orm_kit.manager import Manager


class Article(Model):
    __tablename__ = "articles"

    # SQLAlchemy columns directly
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    content = Column(Text)
    published = Column(Boolean, default=False)
    created_at = Column(DateTime)
    # Required for QuerySet API
    objects = Manager()
```

#### Relationships

```python
from sqlalchemy import ForeignKey
from sqlalchemy.orm import relationship

class Author(Model):
    __tablename__ = "authors"

    id = Column(Integer, primary_key=True)
    name = Column(String(100))
    # Relationship (SQLAlchemy style)
    books = relationship("Book", back_populates="author")

    objects = Manager()

class Book(Model):
    __tablename__ = "books"

    id = Column(Integer, primary_key=True)
    title = Column(String(200))
    author_id = Column(Integer, ForeignKey("authors.id"))

    # Relationship
    author = relationship("Author", back_populates="books")
```

#### Key Points

- **Use SQLAlchemy `Column` types directly** - No custom field wrappers
- **`objects = Manager()`** - Set as default QuerySet API access
- **`relationship()`** - Defines traversable paths for filters and loading
- **`__tablename__`** - Explicit table name (auto-generated if not passed)

#### Column Types Reference

| SQLAlchemy Type | Use Case |
|----------------|----------|
| `Integer` | Whole numbers, IDs |
| `String(n)` | Variable-length text with max length |
| `Text` | Unlimited text |
| `Boolean` | True/False values |
| `DateTime` | Timestamps |
| `Float` | Decimal numbers |
| `JSON` | JSON data (PostgreSQL, MySQL 5.7+) |
| `Enum` | Fixed set of values |

#### Common Column Options

```python
# Nullable
name = Column(String(100), nullable=False)

# Default value
active = Column(Boolean, default=True)

# Unique constraint
email = Column(String(255), unique=True)

# Index
username = Column(String(50), index=True)

# Primary key
id = Column(Integer, primary_key=True, autoincrement=True)
```

---

### 3.2 Session & Connection

**Dependencies:** Model

**Purpose:** Manage database connections and transaction boundaries explicitly.

#### Creating a Session

```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

# Create engine (once per application)
engine = create_async_engine(
    "postgresql+asyncpg://user:pass@localhost/dbname",
    echo=True  # Log SQL queries
)

# Create session (per request/operation)
async with AsyncSession(engine) as session:
    # Use session here
    authors = await Author.objects.filter(name="John", session=session)
```

#### Session Lifecycle

```python
# Pattern 1: Context manager (recommended)
async with AsyncSession(engine) as session:
    result = await Book.objects.filter(published=True, session=session)
    # Session auto-closes

# Pattern 2: Manual management
session = AsyncSession(engine)
try:
    result = await Book.objects.filter(published=True, session=session)
finally:
    await session.close()
```

#### Why Explicit Sessions?

- **Async safety** - No thread-local storage in async contexts
- **Clear boundaries** - Know exactly when transactions start/end
- **Testability** - Easy to inject mock sessions
- **No magic** - Explicit is better than implicit

#### Connection Strings

```python
# PostgreSQL
"postgresql+asyncpg://user:pass@localhost:5432/dbname"

# MySQL
"mysql+aiomysql://user:pass@localhost:3306/dbname"

# SQLite
"sqlite+aiosqlite:///path/to/db.sqlite3"
```

---

### 3.3 Manager & QuerySet Entry

**Dependencies:** Model, Session

**Purpose:** Entry point for building queries through the `Model.objects` interface.

#### Basic Usage

```python
# Manager is the entry point
Author.objects  # Returns a Manager bound to Author model

# All queries need a session
authors = await Author.objects.filter(name="John", session=session)
```

#### Manager Responsibilities

- Creates initial `QuerySet` bound to the model
- Provides access to query methods
- No query execution until awaited

#### Custom Managers

```python
class PublishedManager(Manager):
    def get_queryset(self, session):
        # Pre-filter all queries
        return super().get_queryset(session).filter(published=True)

class Article(Model):
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True)
    title = Column(String(200))
    published = Column(Boolean, default=False)

    objects = Manager()  # All articles
    published_objects = PublishedManager()  # Only published

# Usage
all_articles = await Article.objects.all(session=session)
published = await Article.published_objects.all(session=session)
```

#### Key Points

- Manager doesn't execute queries, it creates QuerySets which if be awaited will cause execution
- Custom managers enable reusable query patterns

---

### 3.4 QuerySet API

**Dependencies:** Manager

**Purpose:** Chainable interface for building database queries.

#### Chaining Operations

```python
# QuerySets are lazy - no database hit yet
qs = (
    Book.objects
    .filter(session=session, published=True)
    .exclude(title__icontains="draft")
    .order_by("-created_at")
)

# Execution happens here
books = await qs.all()
```

#### Filtering Methods

```python
# filter() - AND conditions
Book.objects.filter(published=True, author_id=5)

# exclude() - NOT conditions
Book.objects.exclude(title__icontains="test")

# Multiple filters chain with AND
Book.objects.filter(published=True).filter(year__gte=2020)
```

#### Retrieval Methods (Terminal Operations)

```python
# Get all results
books = await Book.objects.filter(published=True, session=session)

# Get first result or None
book = await Book.objects.filter(id=1, session=session).first()

# Get single result (raises if 0 or >1)
book = await Book.objects.filter(id=1, session=session).get()

# Count results
count = await Book.objects.filter(published=True, session=session).count()

# Check existence
exists = await Book.objects.filter(id=1, session=session).exists()
```

#### Ordering

```python
# Ascending
Book.objects.order_by("title")

# Descending (prefix with -)
Book.objects.order_by("-created_at")

# Multiple fields
Book.objects.order_by("author_id", "-created_at")
```

#### Limiting Results

```python
# First 10 results
Book.objects.filter(published=True)[:10]

# Offset and limit (skip 5, take 10)
Book.objects.filter(published=True)[5:15]
```

#### Key Points

- QuerySets are **immutable** - each method returns a new QuerySet
- Queries are **lazy** - no database hit until terminal method called
- Terminal methods: `all()`, `first()`, `get()`, `count()`, `exists()`
- All manager methods require `session` parameter

---

### 3.5 Lookup Expressions

**Dependencies:** QuerySet API

**Purpose:** Filter data using Django-style field lookups with double-underscore syntax.

#### Basic Lookups

```python
# Exact match (default)
Book.objects.filter(title="Python Guide")
Book.objects.filter(title__exact="Python Guide")  # Same

# Case-insensitive exact
Book.objects.filter(title__iexact="python guide")

# Contains
Book.objects.filter(title__contains="Python")
Book.objects.filter(title__icontains="python")  # Case-insensitive

# Starts/ends with
Book.objects.filter(title__startswith="Python")
Book.objects.filter(title__endswith="Guide")
```

#### Comparison Lookups

```python
# Greater than / less than
Book.objects.filter(price__gt=20)
Book.objects.filter(price__gte=20)  # Greater than or equal
Book.objects.filter(price__lt=50)
Book.objects.filter(price__lte=50)  # Less than or equal

# Range (inclusive)
Book.objects.filter(price__range=(10, 50))

# In list
Book.objects.filter(id__in=[1, 2, 3, 4])
```

#### Null Checks

```python
# Is null
Book.objects.filter(published_date__isnull=True)

# Is not null
Book.objects.filter(published_date__isnull=False)
```

#### Supported Lookups

| Lookup | Description | Example |
|--------|-------------|---------|
| `exact` | Exact match (default) | `title__exact="Guide"` |
| `iexact` | Case-insensitive exact | `title__iexact="guide"` |
| `iexact` | Case-insensitive exact | `title__iexact="guide"` |
| `contains` | Contains substring | `title__contains="Python"` |
| `icontains` | Case-insensitive contains | `title__icontains="python"` |
| `startswith` | Starts with | `title__startswith="Python"` |
| `endswith` | Ends with | `title__endswith="Guide"` |
| `gt` | Greater than | `price__gt=20` |
| `gte` | Greater than or equal | `price__gte=20` |
| `lt` | Less than | `price__lt=50` |
| `lte` | Less than or equal | `price__lte=50` |
| `in` | In list | `id__in=[1,2,3]` |
| `range` | Between (inclusive) | `price__range=(10,50)` |
| `isnull` | Is null/not null | `date__isnull=True` |
| `not__in` | Not in list | `id__not__in=[1,2,3]` |
| `between` | Inclusive range alias | `price__between=(10,50)` |


Only explicitly supported predicate operators are available. Ordering, SQL operator
factories, labels, collations, and other non-predicate SQLAlchemy methods are rejected
as filter lookups.

---

### 3.6 Relationship Traversal

**Dependencies:** Lookup Expressions

**Purpose:** Filter across related models using double-underscore path syntax.

#### Basic Traversal

```python
# Filter books by author name
Book.objects.filter(author__name="John Doe")

# Multiple levels
Book.objects.filter(author__publisher__country="USA")

# With lookups
Book.objects.filter(author__name__icontains="john")
```

#### Reverse Relationships

```python
# Filter authors who have published books
Author.objects.filter(books__published=True)

# Count filter
Author.objects.filter(books__id__isnull=False)
```

#### Multiple Conditions on Same Relation

```python
# Books by authors named John with price > 20
Book.objects.filter(
    author__name__icontains="john",
    price__gt=20
)
```

#### Key Points

- Each `__` traverses one relationship level
- Automatically creates JOINs as needed
- Works with both forward (ForeignKey) and reverse (relationship) directions
- Combines with all lookup expressions

---

### 3.7 Loading Strategies

**Dependencies:** Relationship Traversal

**Purpose:** Control how related objects are loaded to optimize query performance.

#### Eager Loading

```python
# Load related authors in same query (JOIN)
books = await Book.objects.select_related("author", session=session)

# Access author without additional query
for book in books:
    print(book.author.name)  # No extra query

# Multiple relations
books = await Book.objects.select_related(
    "author",
    "publisher",
    session=session
)

# Nested relations
books = await Book.objects.select_related(
    "author__publisher",
    session=session
)
```

#### Prefetch Loading

```python
# Load reverse relations efficiently (separate query)
authors = await Author.objects.prefetch_related("books", session=session)

# Access books without N+1 queries
for author in authors:
    for book in author.books:  # No extra queries
        print(book.title)
```

#### When to Use Each

| Strategy | Use Case | SQL Pattern |
|----------|----------|-------------|
| `select_related` | Forward ForeignKey, OneToOne | JOIN in single query |
| `prefetch_related` | Reverse relations, ManyToMany | Separate query with IN clause |

#### Performance Impact

```python
# BAD: N+1 queries
books = await Book.objects.all(session=session)
for book in books:
    print(book.author.name)  # Query per book!

# GOOD: 1 query with JOIN
books = await Book.objects.select_related("author", session=session)
for book in books:
    print(book.author.name)  # No extra queries
```

---

### 3.8 Annotations

**Dependencies:** QuerySet API

**Purpose:** Add computed fields to query results using database expressions.

#### Basic Annotations

```python
from sqlalchemy import func

# Count related objects
authors = await Author.objects.annotate(
    session=session, book_count=Count("books__id")
)

for author in authors:
    print(f"{author.name}: {author.book_count} books")
```

#### Aggregation Functions

```python
# Sum, average, min, max
books = await Book.objects.annotate(session=session,
    total_price=Sum("price"),
    avg_rating=Avg("rating"),
    min_price=Min("price"),
    max_price=Max("price")
).all()
```

#### Filtering on Annotations

```python
# Authors with more than 5 books
authors = await Author.objects.annotate(
    session=session,
    book_count=Count("books__id")
).filter(
    book_count__gt=5
)
```

#### Key Points

- Annotations add computed fields to results
- Can filter on annotated fields
- Computed in database, not Python

`aggregate()` returns one dictionary of named aggregates across filtered rows.
See the [current aggregation reference](../advanced/aggregation.rst).

---

### 3.9 Q Objects

**Dependencies:** Filtering

**Purpose:** Build complex queries with OR logic and nested conditions.

#### Basic OR Queries

```python
from async_orm_kit import Q

# Books that are published OR featured
Book.objects.filter(Q(published=True) | Q(featured=True))

# Complex combinations
Book.objects.filter(
    Q(published=True) & (Q(price__lt=20) | Q(featured=True))
)
```

#### Negation

```python
# NOT published
Book.objects.filter(~Q(published=True))

# Neither published nor featured
Book.objects.filter(~Q(published=True) & ~Q(featured=True))
```

#### Operators

| Operator | Meaning | Example |
|----------|---------|---------|
| `&` | AND | `Q(a=1) & Q(b=2)` |
| `\|` | OR | `Q(a=1) \| Q(b=2)` |
| `~` | NOT | `~Q(a=1)` |

---

### 3.10 Transaction Management

**Dependencies:** Session

**Purpose:** Group operations into atomic units with rollback capability.

#### Basic Transaction

```python
async with AsyncSession(engine) as session:
    async with session.begin():
        # All operations in this block are atomic
        await Book.objects.create(title="Book 1", session=session)
        await Book.objects.create(title="Book 2", session=session)
        # Auto-commit on exit, rollback on exception
```

#### Manual Control

```python
async with AsyncSession(engine) as session:
    try:
        await Book.objects.create(title="Book 1", session=session)
        await session.commit()
    except Exception:
        await session.rollback()raise
```

#### Nested Transactions (Savepoints)

```python
async with AsyncSession(engine) as session:
    async with session.begin():
        await Book.objects.create(title="Book 1", session=session)
        async with session.begin_nested():  # Savepoint
            await Book.objects.create(title="Book 2", session=session)
            # Can rollback just this savepoint
```

---

## 4. Internal Pipeline

**Audience:** Contributors who want to understand or modify the ORM internals.

**Purpose:** Explain how a QuerySet transforms into SQL and executes.

---

### 4.1 Pipeline Overview

When you call `Book.objects.filter(session=session, author__name="John")`, here's what happens:

```text
┌─────────────┐
│  QuerySet   │  User builds query with Django-style API
└──────┬──────┘
       │
       ▼
┌─────────────┐
│QueryBuilder │  Processes method calls, builds QueryState
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ QueryState  │  Immutable snapshot of query configuration
└──────┬──────┘
       │
       ▼
┌─────────────┐
│Filter Parser│  Parses "author__name" → path + lookup
└──────┬──────┘
       │
       ▼
┌─────────────┐
│FieldResolver│ Resolves path to SQLAlchemy columns
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ JoinGraph   │  Tracks required JOINs, deduplicates
└──────┬──────┘
       │
       ▼
┌─────────────┐
│Condition    │  Builds SQLAlchemy BinaryExpression
│Builder      │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│QueryCompiler│  Assembles final SQLAlchemy Select
└──────┬──────┘
       │
       ▼
┌─────────────┐
│QueryPlanner │  Runs optimization passes
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Execute   │  session.execute() → Results
└─────────────┘
```

---

### 4.2 QueryState Structure

**Location:** `src/async_orm_kit/queryset/state.py`

**Purpose:** Immutable container holding all query configuration.

```python
@dataclass(frozen=True)
class QueryState:
    """Immutable query configuration snapshot."""

    # Core query components
    model: Type[Model]
    filters: Tuple[BinaryExpression, ...]  # Pre-built SQLAlchemy conditions
    annotations: Dict[str, Any]
    order_by: Tuple[str, ...]

    # Loading strategies
    select_related: Tuple[str, ...]
    prefetch_related: Tuple[str, ...]

    # Result modifiers
    distinct: bool
    limit: Optional[int]
    offset: Optional[int]

    # Join tracking
    join_graph: JoinGraph
```

**Key Points:**

- **Frozen dataclass** = immutable, safe to pass around
- **Filters are pre-built** = SQLAlchemy expressions, not strings
- **JoinGraph included** = tracks all relationship traversals
- Each QuerySet method returns new QueryState (copy-on-write)

**Why Immutable?**

```python
# Safe to reuse base queries
base = Book.objects.filter(published=True)
cheap = base.filter(price__lt=10)  # Doesn't modify base
expensive = base.filter(price__gt=50)  # Independent query
```

---

### 4.3 Filter Resolution Pipeline

**Location:** `src/async_orm_kit/query/lookups/parser.py`

**Purpose:** Transform `author__name__icontains="john"` into SQLAlchemy expressions.

#### Stage 1: Filter Parser

```python
class FilterParser:
    """Splits filter key into path and lookup."""

    def parse(self, key: str) -> FilterSpec:
        # "author__name__icontains" → ["author", "name"], "icontains"
        parts = key.split("__")
        # Last part might be lookup
        if parts[-1] in LOOKUPS:
            lookup = parts[-1]
            path = parts[:-1]
        else:
            lookup = "exact"  # Default
            path = parts
        return FilterSpec(path=path, lookup=lookup)
```

**Output:** `FilterSpec(path=["author", "name"], lookup="icontains")`

#### Stage 2: Field Resolver

```python
class FieldResolver:
    """Resolves path to SQLAlchemy column, tracks JOINs."""

    def resolve(self, model: Type[Model], path: List[str],
                join_graph: JoinGraph) -> Column:
        current_model = model

        for segment in path:
            if is_relationship(current_model, segment):
                # Track JOIN needed
                join_graph.add_join(current_model, segment)
                current_model = get_related_model(current_model, segment)
            else:
                # Final field
                return getattr(current_model, segment)
```

**Output:** SQLAlchemy `Column` object + updated `JoinGraph`

**Context Propagation:**

```python
# Multiple filters on same relation
Book.objects.filter(
    author__name="John",      # Creates author JOIN
    author__country="USA"     # Reuses same JOIN
)

# JoinGraph tracks: {("author",): JoinNode(...)}
# Both filters reference same JOIN
```

#### Stage 3: Condition Builder

```python
class ConditionBuilder:
    """Builds SQLAlchemy BinaryExpression from lookup."""

    def build(self, column: Column, lookup: str, value: Any) -> BinaryExpression:
        if lookup == "exact":
            return column == value
        elif lookup == "icontains":
            return column.ilike(f"%{value}%")
        elif lookup == "gt":
            return column > value
        elif lookup == "in":
            return column.in_(value)
        # ... etc
```

**Output:** SQLAlchemy `BinaryExpression` (e.g., `Author.name.ilike("%john%")`)

#### Complete Flow Example

```python
# Input
filter_key = "author__name__icontains"
value = "john"

# Stage 1: Parse
spec = FilterSpec(path=["author", "name"], lookup="icontains")

# Stage 2: Resolve
column = Author.name  # SQLAlchemy Column
join_graph.add_join(Book, "author")  # Track JOIN

# Stage 3: Build
condition = Author.name.ilike("%john%")  # BinaryExpression

# Stored in QueryState
state = QueryState(
    filters=(condition,),
    join_graph=join_graph
)
```

---


### 4.4 JoinGraph Mechanics

**Location:** `src/async_orm_kit/query/relations/`

**Purpose:** Manage the directed acyclic graph of JOIN operations — path-based registration, topological ordering, usage tracking, type promotion, and tree traversal.

#### Architecture Overview

The JoinGraph is a **tree rooted at a synthetic node** representing the base model's FROM clause. Every relationship traversal (e.g., `author`, `building__city__country`) becomes a chain of `JoinNode` children hanging off this root. The graph is the single source of truth for all JOIN operations in a query.

```text
<root> (Product)
├── category [LEFT]
│   └── parent [INNER] [filter]
└── supplier [LEFT]
    └── country [LEFT] [filter]
```

**Key design decisions:**

- **Path-based addressing:** Nodes are keyed by double-underscore paths (`"building__city__country"`), matching Django's lookup syntax.
- **Automatic ancestor creation:** Adding `"a__b__c"` ensures `"a"` and `"a__b"` exist first.
- **OR-merge semantics for usage flags:** Once a flag is `True`, it stays `True` — ensures conservative JOIN optimization.
- **Two protocol interfaces:** `JoinGraphReadable` (read-only for planning) and `JoinGraphMutable` (narrow mutation for JOIN promotion passes).

#### Structure

```python
class JoinGraph:
    """Tree structure tracking all JOINs for a query."""

    __slots__ = (
        "_root_model",    # SQLAlchemy model class (FROM clause)
        "_root",          # Synthetic root JoinNode (path="")
        "_path_registry", # OrderedDict[str, JoinNode] — O(1) lookup by path
        "_join_order",    # List[JoinNode] — topological emission order
    )

    def __init__(self, root_model: type) -> None:
        self._root_model = root_model
        self._root = JoinNode(path="", join_type=JoinType.INNER, alias=root_model)
        self._path_registry: OrderedDict[str, JoinNode] = OrderedDict()
        self._join_order: List[JoinNode] = []
```

**Properties:**

| Property | Type | Description |
|---|---|---|
| `root` | `JoinNode` | Synthetic root node (`path=""`, `alias=root_model`) |
| `root_model` | `type` | SQLAlchemy model for relationship resolution |
| `join_order` | `Sequence[JoinNode]` | Topologically ordered nodes (parents before children) |
| `size` | `int` | Number of JOIN nodes (excluding root) |
| `is_empty` | `bool` | `True` if no JOINs registered |
| `paths` | `Sequence[str]` | All registered paths in insertion order |

#### Key Operations

**1. Add / Merge Node — `add_node()`**

The primary method for registering JOINs. Handles three cases: returning existing nodes with merged flags, creating missing ancestors, and creating the new leaf node.

```python
def add_node(
    self,
    path: str,
    relationship: Optional[RelationshipProperty] = None,
    join_type: JoinType = JoinType.LEFT,
    *,
    used_in_filter: bool = False,
    used_in_or_context: bool = False,
    has_nullable_check: bool = False,
    used_in_negation: bool = False,
    used_in_order_by: bool = False,
    used_in_annotation: bool = False,
) -> JoinNode:
```

**Behavior:**

```python
# 1. Path already exists → merge flags (OR semantics), return existing
existing.used_in_filter = existing.used_in_filter or used_in_filter
# ... same for all flags

# 2. Ancestors missing → auto-create via _ensure_ancestors()
# Adding "category__parent__grandparent" creates:
#   "category" → "category__parent" → "category__parent__grandparent"

# 3. New node → resolve relationship, create alias, link into tree
alias = aliased(relationship.mapper.class_)
node = JoinNode(path=path, alias=alias, join_type=join_type, ...)
parent.add_child(node)
self._path_registry[path] = node
self._join_order.append(node)
```

**Example:**

```python
graph = JoinGraph(Product)

# Simple JOIN
node = graph.add_node("category", used_in_filter=True)
# graph.size == 1

# Nested JOIN — auto-creates "category" ancestor if missing
node = graph.add_node("category__parent__grandparent", used_in_filter=True)
# graph.size == 3 (category, category__parent, category__parent__grandparent)

# Re-adding merges flags (never narrows)
node = graph.add_node("category", used_in_order_by=True)
# node.used_in_filter == True  (preserved from first call)
# node.used_in_order_by == True (added)
```

**2. Remove Leaf Node — `remove_node()`**

Only leaf nodes (no children) can be removed. Attempting to remove a non-leaf raises `ValueError`.

```python
def remove_node(self, path: str) -> bool:
    """Remove a leaf node. Returns True if removed, False if not found."""
    node = self._path_registry.get(path)
    if node is None:
        return False
    if node.children:
        raise ValueError(f"Cannot remove non-leaf node '{path}'")

    # Unlink from parent, remove from registry and join_order
    ...
```

**3. Set JOIN Type — `set_join_type()` (JoinGraphMutable interface)**

The narrow mutation interface exposed to optimization passes (specifically `JoinPromotionPass`).

```python
def set_join_type(self, path: str, join_type: str) -> None:
    """
    Change join type of an existing node.

    Args:
        path: Must be an existing path.
        join_type: 'INNER' or 'LEFT'.

    Raises:
        KeyError: If path does not exist.
        ValueError: If join_type is not recognized.
    """
```

**4. Relationship Resolution**

Two-tier resolution: first checks the node's cached relationship, then walks the model hierarchy via SQLAlchemy introspection.

```python
def resolve_relationship(self, path: str) -> Optional[RelationshipProperty]:
    """Check node cache first, then walk model chain."""
    node = self._path_registry.get(path)
    if node is not None and node.relationship is not None:
        return node.relationship
    return self._resolve_relationship_from_model(path)

def _resolve_relationship_from_model(self, path: str):
    """Walk model chain: 'building__city__country' →
       RootModel.building → BuildingModel.city → CityModel.country"""
    segments = path.split("__")
    current_model = self._root_model
    for segment in segments:
        mapper = sa_inspect(current_model)
        rel = mapper.relationships.get(segment)
        if rel is None:
            return None
        current_model = rel.mapper.class_
    return rel
```

#### Usage Flag Tracking

Usage flags drive JOIN optimization. They are **merged with OR semantics** — once `True`, they never revert to `False`.

| Flag | Set When | Effect on Optimization |
|---|---|---|
| `used_in_filter` | WHERE clause references this JOIN | Enables LEFT → INNER promotion |
| `used_in_or_context` | Filter is inside `Q(...) \| Q(...)` | **Prevents** LEFT → INNER promotion |
| `has_nullable_check` | Filter is `IS NULL` / `IS NOT NULL` | **Prevents** LEFT → INNER promotion |
| `used_in_negation` | Filter is inside `~Q()` / `exclude()` | **Prevents** LEFT → INNER promotion |
| `used_in_order_by` | ORDER BY references this JOIN | Keeps JOIN in query |
| `used_in_annotation` | `annotate()` references this JOIN | Keeps JOIN in query |

**Why this matters:**

```python
# Filter usage → INNER JOIN safe (promotion allowed)
Book.objects.filter(author__name="John")
# SELECT * FROM book INNER JOIN author ON ... WHERE author.name = 'John'

# OR context → LEFT JOIN must stay (promotion blocked)
Book.objects.filter(Q(author__name="John") | Q(title="Django"))
# SELECT * FROM book LEFT JOIN author ON ... WHERE author.name = 'John' OR title = 'Django'

# Nullable check → LEFT JOIN required (promotion blocked)
Book.objects.filter(author__isnull=True)
# SELECT * FROM book LEFT JOIN author ON ... WHERE author.id IS NULL

# Annotation → may need LEFT JOIN to preserve row count
Book.objects.annotate(author_count=Count("author"))
# SELECT book.*, COUNT(author.id) FROM book LEFT JOIN author ON ...
```

#### Traversal Methods

| Method | Strategy | Use Case |
|---|---|---|
| `walk_bfs()` | Breadth-first from root's children | SQL JOIN emission order |
| `walk_dfs()` | Depth-first from root's children | Subtree analysis |
| `subtree(path)` | All descendants including node itself | Cascade operations |
| `leaf_nodes()` | Nodes with no children | Pruning candidates |

```python
# BFS traversal (suitable for SQL emission)
for node in graph.walk_bfs():
    print(f"{node.path} -> {node.join_type.value}")

# Get subtree for analysis
nodes = graph.subtree("category")
# Returns: [category, category__parent, category__parent__grandparent]
```

#### Query Information Methods

```python
graph.filter_nodes()   # Nodes used in WHERE clauses
graph.inner_joins()    # Nodes with INNER join type
graph.left_joins()     # Nodes with LEFT join type
graph.max_depth()      # Deepest join chain length
graph.has_node(path)   # O(1) existence check
graph.get_node(path)   # O(1) node retrieval (or None)
```

#### Validation

The `validate()` method runs four integrity checks and returns a list of error messages (empty = valid):

```python
errors = graph.validate()
# Checks:
# 1. Every node's parent reference points to an existing node (or root)
# 2. No orphaned nodes (every node reachable from root via BFS)
# 3. Join order is topologically valid (parent appears before child)
# 4. No broken linkage in registry
```

#### Cloning and Lifecycle

```python
# Deep clone — reuses alias & relationship objects, preserves tree structure
new_graph = graph.clone()

# Reset to empty state (keeps root model)
graph.clear()
```

`clone()` rebuilds the full parent-child tree by walking `join_order` and re-linking nodes via a node map. This is used by optimization passes that need to experiment with changes without mutating the original graph.

#### Performance Characteristics

| Operation | Complexity | Notes |
|---|---|---|
| `has_node()` / `get_node()` | $O(1)$ | Dictionary lookup |
| `add_node()` | $O(d)$ | $d$ = depth; ancestor creation + model introspection |
| `remove_node()` | $O(n)$ | Rebuilds `_join_order` list |
| `walk_bfs()` / `walk_dfs()` | $O(n)$ | $n$ = total nodes |
| `validate()` | $O(n)$ | Full graph traversal |
| `clone()` | $O(n)$ | Rebuilds tree structure |

#### Debugging

```python
# ASCII tree visualization
print(graph.to_ascii_tree())
# <root> (Product)
# ├── category [LEFT]
# │   └── parent [INNER] [filter]
# └── supplier [LEFT]
#     └── country [LEFT] [filter, order_by]

# Repr
repr(graph)  # JoinGraph(model=Product, joins=4)

# Supports len() and in
len(graph)                   # 4
"category__parent" in graph  # True
```

---

### 4.5 QueryCompiler Assembly

**Location:** `src/async_orm_kit/query/compiler.py`

**Purpose:** Build final SQLAlchemy `Select` statement.

```python
class QueryCompiler:
    """Assembles SQLAlchemy Select from QueryState."""

    def compile(self, state: QueryState) -> Select:
        # Start with base select
        stmt = select(state.model)

        # Add JOINs
        stmt = self._apply_joins(stmt, state.join_graph)

        # Add WHERE clause
        stmt = self._apply_filters(stmt, state.filters)

        # Add annotations (SELECT expressions)
        stmt = self._apply_annotations(stmt, state.annotations)

        # Add ORDER BY
        stmt = self._apply_ordering(stmt, state.order_by)

        # Add LIMIT/OFFSET
        if state.limit:
            stmt = stmt.limit(state.limit)
        if state.offset:
            stmt = stmt.offset(state.offset)

        # Add DISTINCT
        if state.distinct:
            stmt = stmt.distinct()

        return stmt
```

#### Join Application

```python
def _apply_joins(self, stmt: Select, join_graph: JoinGraph) -> Select:
    for node in join_graph.get_ordered_joins():
        relationship = getattr(node.source_model, node.relationship)

        if node.join_type == "left":
            stmt = stmt.outerjoin(relationship)
        else:
            stmt = stmt.join(relationship)

    return stmt
```

#### Filter Application

```python
def _apply_filters(self, stmt: Select, filters: Tuple[BinaryExpression, ...]) -> Select:
    for condition in filters:
        stmt = stmt.where(condition)
    return stmt
```

#### Annotation Application

```python
def _apply_annotations(self, stmt: Select, annotations: Dict[str, Any]) -> Select:
    for name, expr in annotations.items():
        stmt = stmt.add_columns(expr.label(name))
    return stmt
```

---

### 4.7 Execution Flow

**Location:** `src/async_orm_kit/queryset/executor.py`

```python
class QueryExecutor:
    """Executes compiled query and processes results."""

    async def execute(self, stmt: Select, session: AsyncSession) -> List[Model]:
        # Execute query
        result = await session.execute(stmt)

        # Fetch rows
        rows = result.all()

        # Convert to model instances
        instances = [self._row_to_instance(row) for row in rows]

        return instances

    def _row_to_instance(self, row: Row) -> Model:
        # Extract model data from row
        # Handle annotations as extra attributes
        # Return model instance
        pass
```

---

### 4.8 Complete Example Trace

Let's trace: `Book.objects.filter(author__name="John").annotate(page_count=func.count(Book.pages)).all(session=session)`

```python
# 1. QueryBuilder processes calls
builder = QueryBuilder(Book)
builder = builder.filter(author__name="John")
builder = builder.annotate(page_count=func.count(Book.pages))

# 2. Creates QueryState
state = QueryState(
    model=Book,
    filters=(Author.name == "John",),  # Pre-built
    annotations={"page_count": func.count(Book.pages)},
    join_graph=JoinGraph(nodes={
        ("author",): JoinNode(
            source_model=Book,
            relationship="author",
            target_model=Author,
            join_type="inner",used_in_filter=True,
            used_in_annotation=False
        )
    })
)

# 3. QueryPlanner optimizes
state = planner.plan(state)
# No changes needed (JOIN used in filter, stays INNER)

# 4. QueryCompiler assembles
stmt = compiler.compile(state)
# SELECT book.*, COUNT(book.pages) AS page_count
# FROM book
# INNER JOIN author ON book.author_id = author.id
# WHERE author.name = 'John'
# GROUP BY book.id

# 5. QueryExecutor runs
results = await executor.execute(stmt, session)
# Returns List[Book] with page_count attribute
```

---

## 5. Common Patterns & Recipes

**Purpose:** Practical examples and best practices for everyday tasks.

---

### 5.1 Pagination

#### Offset-Based Pagination

```python
async def get_page(page: int, page_size: int = 20):
    offset = (page - 1) * page_size
    books = await Book.objects.limit(page_size).offset(offset).all(session=session)
    total = await Book.objects.count(session=session)

    return {
        "items": books,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size
    }
```

#### Cursor-Based Pagination (Better Performance)

```python
async def get_next_page(last_id: Optional[int] = None, page_size: int = 20):
    query = Book.objects.order_by("id")

    if last_id:
        query = query.filter(id__gt=last_id)

    books = await query.limit(page_size).all(session=session)
    return {
        "items": books,
        "next_cursor": books[-1].id if books else None,
        "has_more": len(books) == page_size
    }
```

---

### 5.2 Complex Filtering

#### Multiple OR Conditions

```python
from async_orm_kit import Q

# Books that match any condition
books = await Book.objects.filter(
    Q(featured=True) |
    Q(price__lt=10) |
    Q(author__name__icontains="bestseller"),
    session=session,
)
```

#### Combining AND/OR

```python
# (published AND price < 20) OR featured
books = await Book.objects.filter(
    (Q(published=True) & Q(price__lt=20)) | Q(featured=True), session=session,
)
```

#### Excluding Records

```python
# All books except those by specific author
books = await Book.objects.filter(
    ~Q(author__name="John Doe", session=session)
)
```

#### Date Range Queries

```python
from datetime import datetime, timedelta

# Books published in last 30 days
thirty_days_ago = datetime.now() - timedelta(days=30)
recent_books = await Book.objects.filter(
    published_date__gte=thirty_days_ago, session=session
)

# Books published in specific year
books_2025 = await Book.objects.filter(
    published_date__year=2025, session=session
)
```

---

### 5.3 Aggregation Patterns

#### Group By with Aggregates

```python
from sqlalchemy import func

# Count books per author
author_stats = await Author.objects.annotate(
    book_count=Count("books"),
    avg_price=Avg("price"),
    total_revenue=Sum("price"),
    session=session,
)

for author in author_stats:
    print(f"{author.name}: {author.book_count} books, avg ${author.avg_price}")
```

#### Conditional Aggregation

```python
# Count only published books
authors = await Author.objects.annotate(session=session,
    published_count=Count("books").filter(published=True)
)
```

#### Having Clause (Filter on Aggregates)

```python
# Authors with more than 5 books
prolific_authors = await Author.objects.annotate(
    book_count=Count("books"), session=session
).filter(
    book_count__gt=5
)
```

---

### 5.4 Optimizing N+1 Queries

#### Problem: N+1 Query

```python
# BAD: Triggers 1 + N queries
books = await Book.objects.all(session=session)  # 1 query
for book in books:
    print(book.author.name)  # N queries (one per book)
```

#### Solution 1: select_related

```python
# GOOD: Single query with JOIN
books = await Book.objects.select_related("author", session=session)
for book in books:
    print(book.author.name)  # No extra queries
```

#### Solution 2: prefetch_related

```python
# For reverse relations
authors = await Author.objects.prefetch_related("books", session=session)
for author in authors:
    for book in author.books:  # No extra queries
        print(book.title)
```

#### Multiple Relations

```python
# Load multiple relations efficiently
books = await Book.objects.select_related(
    "author",
    "publisher",
    "author__publisher",  # Nested
    session=session
)
```

---

### 5.5 Subqueries and Exists

#### Subquery in Filter

```python
from sqlalchemy import select

# Books by authors who have published > 5 books
prolific_author_ids = Author.objects.values("id").annotate(total_books=Count("books")).having(total_books__gte=5)

books = await Book.objects.filter(
    session=session,
    author_id__in=prolific_author_ids
)
```

#### Exists Check

```python
# Authors who have at least one published book
authors_with_books = await Author.objects.filter(
    session=session,
    books__published=True
).distinct()
```

---
