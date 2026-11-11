> **Status: historical design notes, not a verified API reference.** These documents were extracted from the original application. Some examples and design descriptions may not match the current implementation. Use the package source and README as the source of truth; the guides will be reconciled in the documentation/release phase.

# Migration Guide: SQLAlchemy to Django-Style ORM

This guide helps you migrate from SQLAlchemy to our Django-style ORM while maintaining similar functionality.

---

## Quick Comparison

| Feature | SQLAlchemy | Our ORM |
|---------|-----------|---------|
| Model Definition | Declarative Base | Declarative Base (same) |
| Query Interface | `session.query(Model)` | `Model.objects` |
| Filtering | `.filter(Model.field == value)` | `.filter(field=value)` |
| Chaining | `.filter().filter()` | `.filter().filter()` |
| Execution | `.all()`, `.first()` | `.all(session=session)`, `.first(session=session)` |
| Relationships | `.options(joinedload())` | `.select_related()` |
| Aggregation | `func.count()` | `Count()` |

---

## 1. Model Definition

### SQLAlchemy
```python
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class Author(Base):
    __tablename__ = "authors"

    id = Column(Integer, primary_key=True)
    name = Column(String(100))
    books = relationship("Book", back_populates="author")

class Book(Base):
    __tablename__ = "books"

    id = Column(Integer, primary_key=True)
    title = Column(String(200))
    price = Column(Integer)
    author_id = Column(Integer, ForeignKey("authors.id"))
    author = relationship("Author", back_populates="books")
```
### Our ORM
```python
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from async_orm_kit.models import Model

class Author(Model):
    # remove if accept auto generated name
    __tablename__ = "authors"

    id = Column(Integer, primary_key=True)
    name = Column(String(100))
    books = relationship("Book", back_populates="author")


class Book(Model):
    __tablename__ = "books"

    id = Column(Integer, primary_key=True)
    title = Column(String(200))
    price = Column(Integer)
    author_id = Column(Integer, ForeignKey("authors.id"))
    author = relationship("Author", back_populates="books")

```
**Migration Steps:**
1. Keep your existing SQLAlchemy model definitions, just inherit from our base `Model`, also use the `Base` from `async_orm_kit.models.meta` as declarative base.
2. No other changes needed

---

## 2. Basic Queries

### SQLAlchemy
```python
# Get all
books = await session.execute(select(Book))
books = books.scalars().all()

# Filter
stmt = select(Book).where(Book.price > 20)
books = await session.execute(stmt)
books = books.scalars().all()

# Get single
stmt = select(Book).where(Book.id == 1)
book = await session.execute(stmt)
book = book.scalar_one()

# Count
stmt = select(func.count()).select_from(Book)
count = await session.execute(stmt)
count = count.scalar()
```
### Our ORM
```python
# Get all
books = await Book.objects.all(session=session)

# Filter
books = await Book.objects.filter(session=session, price__gt=20)

# Get single
book = await Book.objects.get(session=session, id=1)

# Count
count = await Book.objects.count(session=session)
```
**Key Differences:**
- No need to import `select()` or `func`
- Use field lookups (`price__gt`) instead of operators (`Book.price >`)
- Pass `session` to the first method of the manager.
- `get()` raises `DoesNotExist`

---

## 3. Field Lookups

### SQLAlchemy
```python
# Exact match
Book.price == 20

# Greater than
Book.price > 20

# Less than or equal
Book.price <= 20

# IN clause
Book.id.in_([1, 2, 3])

# IS NULL
Book.author_id.is_(None)

# LIKE
Book.title.like("%Python%")
```
### Our ORM
```python
# Exact match
filter(price=20)
# or
filter(price__exact=20)

# Greater than
filter(price__gt=20)

# Less than or equal
filter(price__lte=20)

# IN clause
filter(id__in=[1, 2, 3])

# IS NULL
filter(author_id__isnull=True)

# LIKE (case-insensitive)
filter(title__icontains="Python")
```

---

## 4. Chaining Filters

### SQLAlchemy
```python
stmt = (
    select(Book)
    .where(Book.price > 20)
    .where(Book.author_id.is_not(None))
    .order_by(Book.title)
    .limit(10)
)
books = await session.execute(stmt)
books = books.scalars().all()
```
### Our ORM
```python
books = await (
    Book.objects
    .filter(session=session, price__gt=20)
    .filter(author_id__isnull=False)
    .order_by("title")
    .limit(10)
)
```
**Key Differences:**
- Each method returns a new QuerySet (immutable)
- No need to call `execute()` and `scalars()`

---

## 5. Relationships & Eager Loading

### SQLAlchemy
```python
from sqlalchemy.orm import joinedload, selectinload

# Joined load (single query with JOIN)
stmt = select(Book).options(joinedload(Book.author))
books = await session.execute(stmt)
books = books.unique().scalars().all()

# Select in load (separate query)
stmt = select(Book).options(selectinload(Book.author))
books = await session.execute(stmt)
books = books.scalars().all()
```
### Our ORM
```python
# Joined load (single query with JOIN)
books = await (
    Book.objects
    .select_related("author")
    .all(session=session)
)

# Select in load (separate query)
books = await (
    Book.objects
    .prefetch_related("author")
    .all(session=session)
)
```
**Key Differences:**
- `select_related()` = `joinedload()` (for ForeignKey)
- `prefetch_related()` = `selectinload()` (for Many-to-Many)
- No need to call `.unique()` with select_related

---

## 6. Relationship Traversal

### SQLAlchemy
```python
# Filter by related field
stmt = select(Book).join(Book.author).where(Author.name == "John")
books = await session.execute(stmt)
books = books.scalars().all()

# Order by related field
stmt = select(Book).join(Book.author).order_by(Author.name)
books = await session.execute(stmt)
books = books.scalars().all()
```
### Our ORM
```python
# Filter by related field
books = await (
    Book.objects
    .filter(
        session=session,
        author__name="John"
    )
)

# Order by related field
books = await (
    Book.objects
    .order_by(session=session, "author__name")
    .all()
)
```
**Key Differences:**
- Use double underscore (`__`) to traverse relationships
- No need to explicitly call `.join()`
- Joins are added automatically

---

## 7. Aggregation

### SQLAlchemy
```python
from sqlalchemy import func

# Count
stmt = select(func.count()).select_from(Book)
count = await session.execute(stmt)
count = count.scalar()

# Sum
stmt = select(func.sum(Book.price))
total = await session.execute(stmt)
total = total.scalar()

# Average
stmt = select(func.avg(Book.price))
avg = await session.execute(stmt)
avg = avg.scalar()

# With grouping
stmt = (
    select(Book.author_id, func.count())
    .group_by(Book.author_id)
)
results = await session.execute(stmt)
results = results.all()
```
### Our ORM
```python
from async_orm_kit.query.expressions import Count, Sum, Avg

# Count
count = await Book.objects.count(session=session)

# Sum (using annotate)
result = await (
    Book.objects
    .annotate(session=session, total=Sum("price"))
    .values("total")
    .first()
)
total = result["total"]

# Average (using annotate)
result = await (
    Book.objects
    .annotate(session=session, avg_price=Avg("price"))
    .values("avg_price")
    .first()
)
avg = result["avg_price"]

# With grouping
results = await (
    Book.objects
    .values(session=session, "author_id")
    .annotate(book_count=Count("id"))
)
```
**Key Differences:**
- `count()` is a terminal operation
- Use `annotate()` for computed fields
- Use `values()` for grouping
- Import aggregation functions from `query.expressions`

---

## 8. Complex Queries with Q Objects

### SQLAlchemy
```python
from sqlalchemy import or_, and_

# OR condition
stmt = select(Book).where(
    or_(Book.price > 50, Book.author_id == 1)
)

# Complex nested
stmt = select(Book).where(
    and_(
        Book.price > 20,
        or_(Book.author_id == 1, Book.author_id == 2)
    )
)
```
### Our ORM
```python
from async_orm_kit.query.q import Q

# OR condition
books = await (
    Book.objects
    .filter(session=session, Q(price__gt=50) | Q(author_id=1))
)

# Complex nested
books = await (
    Book.objects
    .filter(session=session,
        Q(price__gt=20) &
        (Q(author_id=1) | Q(author_id=2))
    )
)
```
**Key Differences:**
- Use `Q()` objects instead of `or_()` and `and_()`
- Use `|` for OR, `&` for AND, `~` for NOT
- More readable with field lookups

---

## 9. F Objects (Field References)

### SQLAlchemy
```python
# Compare two fields
stmt = select(Book).where(Book.price > Book.discount_price)

# Update with field reference
stmt = update(Book).values(price=Book.price * 1.1)
await session.execute(stmt)
```
### Our ORM
```python
from async_orm_kit.query.expressions import F

# Compare two fields
books = await (
    Book.objects
    .filter(session=session, price__gt=F("discount_price"))
)

```
**Key Differences:**
- Wrap field names in `F()` for references
- Can use in filters
- Supports arithmetic operations

---

## 10. Case/When Expressions

### SQLAlchemy
```python
from sqlalchemy import case

# Conditional expression
stmt = select(
    Book,
    case(
        (Book.price > 50, "expensive"),
        (Book.price > 20, "moderate"),
        else_="cheap"
    ).label("price_category")
)
```
### Our ORM
```python
from async_orm_kit.query.expressions import Case, When, Value

# Conditional expression
books = await (
    Book.objects
    .annotate(
        session=session,
        price_category=Case(
            When(price__gt=50, then=Value("expensive")),
            When(price__gt=20, then=Value("moderate")),
            default=Value("cheap")
        )
    )
)
```
**Key Differences:**
- Use `Case()` and `When()` objects
- Use `Value()` for literal values
- Access via `book.price_category` after annotation

---

## 11. Mutations

### SQLAlchemy
```python
# Create
book = Book(title="New Book", price=30)
session.add(book)
await session.commit()

# Bulk create
books = [Book(title=f"Book {i}", price=i*10) for i in range(100)]
session.add_all(books)
await session.commit()

# Update
stmt = update(Book).where(Book.id == 1).values(price=25)
await session.execute(stmt)
await session.commit()

# Delete
stmt = delete(Book).where(Book.id == 1)
await session.execute(stmt)
await session.commit()
```
### Our ORM
```python
# Create
book = await Book.objects.create(
    session=session,
    title="New Book",
    price=30
)

# Bulk create
books = [{"title": f"Book {i}", "price": i*10} for i in range(100)]
await Book.objects.bulk_create(session=session, objects=books)

# Update
await Book.objects.filter(session=session, id=1).update(price=25)

# Delete
await Book.objects.filter(session=session, id=1).delete()
```
**Key Differences:**
- `create()` returns the created instance
- `bulk_create()` takes list of dicts
- `update()` and `delete()` work on QuerySets
- ORM mutations flush/execute inside the caller-owned transaction; commit and rollback remain application responsibilities

---

## 12. Distinct & Values

### SQLAlchemy
```python
# Distinct
stmt = select(Book.author_id).distinct()
author_ids = await session.execute(stmt)
author_ids = author_ids.scalars().all()

# Select specific fields
stmt = select(Book.title, Book.price)
results = await session.execute(stmt)
results = results.all()
```
### Our ORM
```python
# Distinct
author_ids = await (
    Book.objects
    .values("author_id", session=session)
    .distinct()
)

# Select specific fields
results = await (
    Book.objects
    .values("title", "price", session=session)
)
```

**Key Differences:**
- `distinct()` can be called on QuerySet
- `values()` returns dicts instead of model instances
- Can combine with `distinct()` for unique combinations

---

## 13. Query Optimization

### SQLAlchemy
```python
# Manual optimization
stmt = (
    select(Book)
    .options(joinedload(Book.author))
    .where(Book.price > 20)
)

# Check query
print(stmt)
```
### Our ORM
```python
# Automatic optimization suggestions
books = await (
    Book.objects
    .filter(price__gt=20)
    .all(session=session)
)

# Get query plan
plan = Book.objects.filter(price__gt=20).explain()
print(plan)
```

# Query planner detects:
# - Missing indexes(if used)
# - N+1 queries
# - Inefficient joins

**Key Differences:**
- Built-in query planner analyzes queries
- Automatic N+1 detection
- `explain()` method for EXPLAIN ANALYZE
- Optimization suggestions in logs

---

## 14. Session Management

### SQLAlchemy
```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

engine = create_async_engine("postgresql+asyncpg://...")
async_session = sessionmaker(engine, class_=AsyncSession)

async with async_session() as session:
    books = await session.execute(select(Book))
    books = books.scalars().all()
```
### Our ORM
```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

engine = create_async_engine("postgresql+asyncpg://...")
async_session = sessionmaker(engine, class_=AsyncSession)

# Same session management
async with async_session() as session:
    books = await Book.objects.all(session=session)
```
**Key Differences:**
- Session management is identical
- Pass session to terminal operations
- Our ORM wraps SQLAlchemy sessions

---

## 15. Common Patterns

### Pattern: Pagination

**SQLAlchemy:**
```python
page = 2
page_size = 20
offset = (page - 1) * page_size

stmt = select(Book).offset(offset).limit(page_size)
books = await session.execute(stmt)
books = books.scalars().all()
```
**Our ORM:**
```python
page = 2
page_size = 20

books = await (
    Book.objects
    .offset((page - 1) * page_size)
    .limit(page_size)
    .all(session=session)
)
```
### Pattern: Exists Check

**SQLAlchemy:**
```python
stmt = select(exists().where(Book.price > 100))
has_expensive = await session.execute(stmt)
has_expensive = has_expensive.scalar()

```
**Our ORM:**
```python
has_expensive = await (
    Book.objects
    .filter(price__gt=100)
    .exists(session=session)
)
```

## Migration Checklist

- [ ] inherit from `Model` class to all model classes
- [ ] Replace `session.query()` with `Model.objects`
- [ ] Replace `select()` statements with QuerySet methods
- [ ] Convert SQLAlchemy operators to field lookups
- [ ] Replace `joinedload()` with `select_related()`
- [ ] Replace `selectinload()` with `prefetch_related()`
- [ ] Convert `or_()` and `and_()` to Q objects
- [ ] Update aggregation to use `annotate()` and `values()`
- [ ] Replace `session.add()` with `create()`
- [ ] Replace `update()` statements with QuerySet `update()`
- [ ] Replace `delete()` statements with QuerySet `delete()`
- [ ] Update exception handling (`NoResultFound` → `DoesNotExist`)
- [ ] Test all queries for correctness
- [ ] Review query plans for optimization

---

## Benefits of Migration

1. **More Readable**: Django-style syntax is cleaner and more intuitive
2. **Type Safe**: Full type checking with protocols
3. **Optimized**: Built-in query planner detects issues
4. **Less Boilerplate**: No need for `select()`, `execute()`, `scalars()`
5. **Better Errors**: Clear exception types and messages
6. **Chainable**: Immutable QuerySets for safer composition

---

## Gradual Migration Strategy

You can migrate incrementally:

1. **Phase 1**: Add `objects` manager to models, keep SQLAlchemy queries
2. **Phase 2**: Migrate simple queries (get, filter, all)
3. **Phase 3**: Migrate complex queries (joins, aggregations)
4. **Phase 4**: Migrate mutations (create, update, delete)
5. **Phase 5**: Remove SQLAlchemy query code

Both styles can coexist during migration since our ORM is built on SQLAlchemy.

---

**Need Help?** Check the API documentation or open an issue on GitHub.


This migration guide provides a comprehensive comparison and practical examples for transitioning from SQLAlchemy to the Django-style ORM.
