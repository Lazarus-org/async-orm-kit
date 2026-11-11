Security considerations
=======================

SQL and untrusted input
-----------------------

Filter values and expression literals use SQLAlchemy bound parameters. Field
paths must resolve to mapped attributes; identifiers are not arbitrary SQL.
Custom lookup handlers and SQL function factories are trusted application code.
Do not build them from request text or concatenate untrusted strings into
SQLAlchemy ``text()`` expressions.

Logging and diagnostics
-----------------------

``QuerySet.sql()`` keeps parameter placeholders. Query profiling does not collect
bound values, but literal sanitization is best effort and is not a data-loss
prevention boundary. Restrict access to query traces and diagnostic exports.

Database failures retain the original exception in ``DatabaseError.cause`` and
exception chaining. Driver exceptions can contain SQL and parameter values.
Automatic query failure logs use generic messages; applications that log the
exception chain must apply their own redaction and retention policy. Configure
``create_async_engine(..., hide_parameters=True)`` when sensitive values may
reach SQLAlchemy's own logs. Avoid logging database URLs or credentials.

Sessions and database permissions
---------------------------------

Use one AsyncSession per task and caller-owned transactions. Roll back after a
failed flush before reusing the session. Keep transactions short and close
streams explicitly when stopping early, for example with
``contextlib.aclosing(query.stream())``. Cancellation is not a commit signal.

Use database accounts with only the application's required permissions and
verified TLS for remote servers. Keep schema changes in reviewed migrations.
Bulk update/delete reject limits, offsets, grouping, distinctness, and joined
relationship filters whose SELECT semantics cannot be preserved by portable
DML. Select identities explicitly before constructing a constrained write.

File storage
------------

Inject a storage backend explicitly. Storage operations and database commits
are separate resources; an application must handle retries and compensation
when one succeeds and the other fails. File keys are validated against path
traversal, but the storage backend is responsible for filesystem isolation,
access control, public URLs, and content validation.
