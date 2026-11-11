# Grouped predicates, JSON paths, and set results

Async ORM Kit 1.0.0 supports composable `Q` predicates over grouped queries,
typed JSON path lookups, and filtering over SQL set-operation projections.

- [Grouped predicates and aggregation](../advanced/aggregation.rst)
- [JSON and collection expressions](../advanced/json-array.rst)
- [Set operations and CTEs](../advanced/sets-cte.rst)

Use explicit projections when filtering set results. Mixed aggregate and row
predicates retain their grouping semantics through correlated SQL expressions.
Native SQLite, PostgreSQL, and MySQL suites verify supported operations;
PostgreSQL-only JSONB and array operators require PostgreSQL.
