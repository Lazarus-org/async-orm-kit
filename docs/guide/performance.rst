Query performance and SQL inspection
====================================

Avoid N+1 queries
-----------------

Use ``select_related`` on scalar relationships and ``prefetch_related`` for
collections. The planner may choose select-in loading for deeper paths using
``ORMConfig(max_joinedload_depth=...)``. Measure actual statements rather
than relying on the number of ORM methods called.

.. code-block:: python

   posts = await (
       Post.objects.all(session).select_related("author")
   )

Query inspection
----------------

.. code-block:: python

   query = User.objects.filter(session, active=True).order_by("id")
   compiled = query.sql()
   plan = await query.explain()

``sql()`` returns a statement for inspection; do not treat it as a fully
interpolated SQL string safe for logging or execution. Bind values remain
separate. ``explain(analyze=True)`` may run the query and incur writes or
locks when misused; only run it under a controlled plan-audit policy.

Profiling
---------

.. code-block:: python

   from async_orm_kit import QueryProfiler

   profiler = QueryProfiler(engine, slow_threshold_ms=75, max_entries=250)
   async with profiler.acapture() as stats:
       records = await User.objects.filter(session, active=True)
   print(stats.stats())

Profile per task/request and keep history bounded. A repeated-statement
fingerprint may indicate N+1 reads but may also be legitimate repeated
queries. A query profiler cannot replace production-scale database metrics.

Pagination and indexes
----------------------

Offset pagination is simple, but a deterministic indexed keyset order is
often more efficient at large offsets. See :doc:`../advanced/pagination`.
Index heavily filtered and ordered columns and review the actual dialect
query plan. Avoid loading large binary objects when only a key or metadata
is needed; use deferred columns and storage streaming.
