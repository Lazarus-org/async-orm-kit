Query profiling and suspected N+1 detection
============================================

``QueryProfiler`` records actual SQLAlchemy cursor events rather than relying
solely on manually tracked QuerySet methods.

.. code-block:: python

   from async_orm_kit import QueryProfiler

   profiler = QueryProfiler(
       engine,
       max_entries=500,
       slow_threshold_ms=100,
   )

   async with profiler.acapture() as profile:
       users = await User.objects.filter(session, active=True)

   print(profile.stats())
   print(profile.n_plus_one_report(threshold=5))

``capture()`` provides a synchronous context-manager form that can span
``await`` expressions; ``acapture()`` provides ``async with``. Attach or
detach persistent listeners using ``attach()`` and ``detach()``. Profile
captures are task-scoped, support nesting, and have bounded retained history.

What is measured
----------------

* Cursor execution timing and errors, not total request time or full ORM
  hydration/serialization latency.
* Sanitized SQL statement patterns and query totals; the profiler intentionally
  avoids collecting parameter values.
* Probable N+1 patterns based on repeated SELECT/WHERE fingerprints, which
  are **heuristic** and not proof of a defect.

Privacy and operational considerations
--------------------------------------

Best-effort SQL sanitization is not a substitute for a secure log-retention
policy. Literal SQL fragments can still reveal schema details. Use a bounded
``max_entries``, limit log access, and avoid indiscriminate profiling of
production traffic. Profile callbacks must be trusted and fast.

The older :class:`async_orm_kit.QueryDebugger` and
:class:`async_orm_kit.NPlusOneDetector` retain their manual APIs and also
support engine listener attachment. See :doc:`../guide/performance` for
loading-strategy profiling.
