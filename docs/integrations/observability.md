# Query observability and N+1 diagnostics

`async-orm-kit` provides **opt-in cursor-level tracing** using SQLAlchemy's
public `Engine` event system. No tracing dependency, background task, custom
session class, or global engine is required. Both synchronous SQLAlchemy
`Engine` and `AsyncEngine` are supported.

## Task-scoped profiling (recommended)

```python
from async_orm_kit import QueryProfiler

# Configure once at application startup. Use the engine that actually executes
# the application's queries; the application owns its lifecycle.
profiler = QueryProfiler(
    engine,
    slow_threshold_ms=75,
    max_entries=500,
)

async def list_users(session):
    with profiler.capture() as profile:
        users = await User.objects.filter(session, is_active=True).order_by("id")

    print(profile.stats())
    print(profile.n_plus_one_report(threshold=5))
    return users
```

`with profiler.capture()` is valid inside an async function. Alternatively:

```python
async with profiler.acapture() as profile:
    users = await User.objects.all(session)
```

Event listeners are installed on entering the first active capture and removed
when the last capture exits. Captures use `contextvars`: requests and concurrent
async tasks have isolated results, and nested captures receive their own record
sets. Tasks explicitly spawned *inside* a capture inherit its context by normal
Python `contextvars` rules. A closed capture cannot be appended to.

The profiler does **not** commit/rollback transactions, own the engine or
session, change query results, or send metrics to an external service.

## What is actually measured?

Each `QueryRecord` contains:

| Attribute | Meaning |
|---|---|
| `sql` | Bounded, best-effort sanitized statement; no captured bound parameters |
| `duration_ms` | DBAPI cursor-execution duration (monotonic clock) |
| `timestamp` | Wall-clock completion timestamp (Unix seconds) |
| `row_count` | Cursor's affected-row count, or `None` if unknown |
| `success` | Whether the driver execution completed without error |
| `error_type` | Exception *class name* on failed cursor execution |

`profile.stats()` returns total queries, elapsed cursor time, average duration,
number of queries exceeding the configured threshold, failed queries, retained
history overflow count and slowest SQL template. Query history and fingerprint
counts are bounded by `max_entries`; execution totals continue to accumulate.
The `untracked_patterns` field indicates fingerprints dropped by that limit.

A SELECT often has `cursor.rowcount == -1`; it is reported as `None`, not as
the number of ORM objects loaded. Timing excludes engine connection acquisition,
ORM result processing, Python computation and overall HTTP latency. Failures
*before* cursor execution (such as checkout or connection errors) are not
measured. One ORM operation can emit several SQL statements (e.g. prefetches);
each statement is counted independently.

## Heuristic N+1 detection

```python
with profiler.capture() as profile:
    for user_id in ids:
        await User.objects.get(session, id=user_id)

suspects = profile.n_plus_one_report(threshold=5)["potential_n_plus_one"]
```

The detector groups normalized SQL templates and flags SELECT statements with
WHERE clauses executed repeatedly. It is a **heuristic**: intentional loops and
polling can trigger warnings, while unusual relationship SQL can be missed. It
does not inspect ORM attributes, identify parent-child ownership or prove that
an N+1 problem exists. Confirm suspects by examining the application loop and
try `select_related()` / `prefetch_related()` when appropriate.

## Legacy opt-in debugging API

The existing `QueryDebugger` and `NPlusOneDetector` retain their manual
`log_query()` and `record_query()` methods. They can now attach directly to a
SQLAlchemy engine to receive actual execution events:

```python
from async_orm_kit import QueryDebugger, NPlusOneDetector

debugger = QueryDebugger(max_entries=500, slow_threshold_ms=250)
detector = NPlusOneDetector(threshold=5)
debugger.attach(engine)
detector.attach(engine)
debugger.enable()
detector.start_monitoring()
try:
    # Execute work with SQLAlchemy or async-orm-kit.
    ...
    print(debugger.get_stats(), detector.report())
finally:
    debugger.disable()
    detector.stop_monitoring()
    debugger.detach()
    detector.detach()
```

Unlike scoped `QueryProfiler` captures, these legacy instances collect events
**across all tasks using the attached engine** while enabled. Avoid a global
monitor in concurrent web apps when you require request isolation.

## Privacy and performance

- Instrumentation is opt-in and adds no DB events unless attached or captured.
- SQLAlchemy bind parameter dictionaries and values are **never read, stored or
  sent to logging**. Literal string/number SQL is sanitized on a best-effort
  basis, and SQL comments are stripped.
- SQL table/column names, quoted identifiers or unusual raw SQL constructs may
  still contain sensitive data; never enable SQL capture for untrusted, private
  queries unless your data policy allows it. Do not log raw statements with
  literal secrets. Use parameterized SQL throughout.
- Event listeners run inside query execution. Keep callbacks fast and avoid
  recursive SQL calls in callbacks; callback exceptions are logged and
  suppressed so application SQL behavior is preserved.
- The `on_query` callback of a persistently attached profiler, like the legacy
  monitor, receives all engine executions, **not just scoped requests**.
- Bounded record/fingerprint history avoids unbounded per-capture growth. Totals
  and counters remain accurate; new distinct fingerprints may be discarded
  once capacity is reached.
- No SQL or connection state is cached by this feature. Profile data is
  application-owned; discard it when the request ends.

## Compatibility and validation

SQLite cursor-level behavior is covered by execution-level regression tests.
A separate `aiosqlite` `AsyncSession` integration suite is provided and required
to run without skips in hosted CI. SQLAlchemy's same event APIs underpin
PostgreSQL (`asyncpg`) and MySQL (`asyncmy`) engines, but live-hosted results
are required before declaring the observability behavior validated there.
