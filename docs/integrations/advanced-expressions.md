# Expressions and advanced queries

Async ORM Kit 1.0.0 supports field arithmetic, conditional expressions,
aggregates, correlated subqueries, window functions, and typed JSON paths.
Expressions resolve to SQLAlchemy constructs and preserve bound parameters.

Use the maintained references for executable examples and restrictions:

- [Expressions](../advanced/expressions.rst)
- [Aggregation](../advanced/aggregation.rst)
- [Correlated subqueries](../advanced/subqueries.rst)
- [Advanced SQL](advanced-sql.md)
- [Extension points and pagination](extensibility.md)

Queries run in the application's SQLAlchemy session. Expressions do not
commit transactions. Driver-specific operators reject unsupported dialects.
