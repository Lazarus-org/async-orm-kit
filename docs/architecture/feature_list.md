> **Status: historical design notes, not a verified API reference.** These documents were extracted from the original application. Some examples and design descriptions may not match the current implementation. Use the package source and README as the source of truth; the guides will be reconciled in the documentation/release phase.

# Feature List & Roadmap

## ✅ Implemented Features (v0.1.0)

### Core ORM
- **Model Definition**: SQLAlchemy-based model classes with `objects` manager
- **Manager Pattern**: `Model.objects` interface for queries
- **QuerySet API**: Chainable query interface with lazy evaluation
- **Filter Lookups**: `exact`, `gt`, `gte`, `lt`, `lte`, `in`, `isnull` (all supported lookups by SQL Alchemy column)
- **Filtering**: `filter()`, `exclude()` with field lookups
- **Ordering**: `order_by()` with ascending/descending
- **Pagination**: `limit()`, `offset()` methods
- **Terminal Operations**: `all()`, `first()`, `get()`, `count()`, `exists()`
- **Async Support**: Full async/await with SQLAlchemy AsyncSession
- **Distinct**: `distinct()` and `distinct(*fields)`
- **Group By**: `values()` with aggregation

### Relationships
- **`select_related()`**: JOIN-based eager loading for ForeignKey
- **`prefetch_related()`**: Separate query eager loading for Many-to-Many
- **Relationship Traversal**: `book__author__name` lookups
- **Reverse Relations**: Access related objects from parent

### Advanced Queries
- **Aggregations**: `Count()`, `Sum()`, `Avg()`, `Min()`, `Max()`
- **Annotations**: `annotate()` with computed fields
- **Q Objects**: Complex query expressions with `Q() & Q() | ~Q()`
- **F Objects**: Field references in queries
- **Case/When**: Conditional expressions with `Case()` and `When()`
- **Value Expressions**: `Value()` for literal values in queries

### Mutations
- **Create**: `create()`, `bulk_create()` methods
- **Update**: `update()`, `bulk_update()` methods
- **Delete**: `delete()` method with cascading

### Query Optimization
- **Query Planning**: Multi-pass optimization framework
- **N+1 Detection**: Identifies missing eager loading
- **Index Suggestions**: Recommends indexes for filters
- **Query Complexity Analysis**: Estimates query cost
- **Query Explain**: EXPLAIN ANALYZE integration

### Documentation
- **Architecture Guide**: System design and component interaction
- **API Reference**: Usage examples and patterns

---

## 📋 Planned Features

### v0.2.0 - Advanced Query Features
- [ ] **Json and Array filters**: `.filter(json_field__has_key="key")`
- [ ] **Subqueries**: `Subquery()` and `Exists()` expressions
- [ ] **Window Functions**: `OVER()` clause support
- [ ] **Union/Intersection**: `union()`, `intersection()`, `difference()`
- [ ] **Aggregate Method**: `aggregate()` for terminal aggregation (currently only `annotate()`)
- [ ] **Output Field**: Custom output field types for `Case()` expressions
- [ ] **Get or Create**: `get_or_create()`, `update_or_create()`
- [ ] **F-Expression support in update**: support `.update(session=session, price=F("price") * 1.1)`


### v0.3.0 - Model Fields
- [ ] **CharField**: Text fields with max_length
- [ ] **TextField**: Unlimited text fields
- [ ] **IntegerField**: Integer fields
- [ ] **FloatField**: Floating point fields
- [ ] **DecimalField**: Precise decimal fields
- [ ] **BooleanField**: Boolean fields
- [ ] **DateField**: Date-only fields
- [ ] **DateTimeField**: Timestamp fields
- [ ] **TimeField**: Time-only fields
- [ ] **JSONField**: JSON/JSONB fields
- [ ] **ArrayField**: PostgreSQL array fields
- [ ] **UUIDField**: UUID fields
- [ ] **EmailField**: Email validation fields
- [ ] **URLField**: URL validation fields
- [ ] **FileField**: File upload fields
- [ ] **ImageField**: Image upload fields
- [ ] **ForeignKey**: One-to-many relationships
- [ ] **ManyToManyField**: Many-to-many relationships
- [ ] **OneToOneField**: One-to-one relationships


### v0.4.0 - Advanced Relationships
- [ ] **Through Models**: Custom M2M intermediary tables
- [ ] **Generic Relations**: ContentType-based polymorphic relations
- [ ] **Self-Referential**: Models with ForeignKey to self
- [ ] **Circular Dependencies**: Lazy relationship resolution

### v0.5.0 - Schema Management
- [ ] **Migrations**: Alembic integration for schema changes
- [ ] **Auto-detection**: Detect model changes automatically
- [ ] **Migration Squashing**: Combine migration files
- [ ] **Data Migrations**: Custom Python migration scripts

### v0.6.0 - Advanced Features
- [ ] **Full-Text Search**: PostgreSQL/MySQL text search
- [ ] **Spatial Data**: PostGIS integration
- [ ] **Custom Lookups**: User-defined lookup types
- [ ] **Custom Aggregates**: User-defined aggregate functions

### v0.7.0 - Developer Experience
- [ ] **Query Debugging**: SQL logging with timing
- [ ] **Admin Interface**: Auto-generated CRUD UI
- [ ] **Performance Profiling**: Query performance tracking

### v0.8.0 - Production Ready
- [ ] **Comprehensive Tests**: 95%+ code coverage
- [ ] **Production Documentation**: Deployment guides

## 📊 Version Strategy

- **v0.x**: Feature development, breaking changes allowed
- **v1.0**: Stable API, semantic versioning
- **v2.0+**: Major features, backward compatibility maintained

## 🤝 Contributing

Priority areas for contributions:
1. Model field implementations (v0.3.0)
2. Subquery support (v0.2.0)
3. Window functions (v0.2.0)
4. Query optimization passes
5. Documentation and examples

---

**Last Updated**: 2026-03-30
**Current Version**: v0.1.0
