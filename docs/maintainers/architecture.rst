Architecture and extension boundaries
=====================================

Main layers
-----------

* **Model / Manager:** SQLAlchemy declarative mapping, bound managers,
  model lifecycle and validation.
* **QuerySet:** immutable facade representing query state and a caller-owned
  async session.
* **QueryBuilder / QueryState:** chainable query clauses, projections,
  pagination, annotations and loading directives.
* **QueryCompiler / resolver:** field lookup parsing, relationship navigation,
  JSON transforms, expression resolution, aggregate analysis and SQLAlchemy
  SELECT expression construction.
* **QueryExecutor / QueryWriter:** SQLAlchemy execution and data modification.
* **Planner:** relationship join decisions, deduplication and eager-loading
  configuration.
* **Integrations:** optional storage, migration and FastAPI adapters.
* **Observability:** cursor-event instrumentation independent of QuerySet's
  manual observer lifecycle.

Boundaries
----------

The package never creates a global ``AsyncSession`` or owns transaction
commit/rollback policy. Mapping, DDL and migrations remain SQLAlchemy/Alembic
responsibilities. Filesystem/cloud storage is not transactionally coordinated
with SQL automatically. Custom expression factories are trusted application
code and must produce SQLAlchemy expressions.

Architecture decisions
----------------------

The architecture decisions in ``docs/architecture/ADR/`` explain design
tradeoffs and their original context. This RST site documents the current
public API.
