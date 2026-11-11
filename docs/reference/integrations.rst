Integration and observability API reference
===========================================

Alembic
-------

.. autofunction:: async_orm_kit.integrations.alembic.get_target_metadata

FastAPI
-------

.. autofunction:: async_orm_kit.integrations.fastapi.create_session_dependency

.. autofunction:: async_orm_kit.integrations.fastapi.register_exception_handlers

Query instrumentation
---------------------

.. autoclass:: async_orm_kit.QueryProfiler
   :members: attach, detach, capture, acapture

.. autoclass:: async_orm_kit.QueryProfile
   :members: stats, n_plus_one_report

.. autoclass:: async_orm_kit.QueryRecord
   :members:

.. autoclass:: async_orm_kit.QueryDebugger
   :members: enable, disable, attach, detach, log_query, get_stats, clear, measure_query

.. autoclass:: async_orm_kit.NPlusOneDetector
   :members: start_monitoring, stop_monitoring, attach, detach, record_query, report

.. autofunction:: async_orm_kit.query.observability.sanitized_sql

.. autofunction:: async_orm_kit.query.observability.sql_fingerprint

Detailed configuration and usage:
:doc:`../advanced/observability`, :doc:`../integrations-rst/fastapi` and
:doc:`../integrations-rst/alembic`.
