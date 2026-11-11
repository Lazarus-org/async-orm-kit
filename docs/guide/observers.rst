Lifecycle observers and hooks
=============================

Observers provide callbacks around model writes and QuerySet operations.
Their purpose is instrumentation and application-side lifecycle work, not
transaction ownership.

Model observers
---------------

``BaseModelObserver`` defines asynchronous ``pre_save``, ``post_save``,
``pre_delete`` and ``post_delete`` callbacks. Model observer registration is
handled by the model metaclass / ``ModelObserverMixin``.

Callbacks after a flush are **not after-commit hooks**. A later rollback can
invalidate the persistence effect even if a ``post_save`` observer already ran.
Do not send irreversible notifications or delete old files from such a hook
without an outbox/commit-aware application mechanism.

QuerySet observers
------------------

``BaseQuerySetObserver`` declares lifecycle callbacks for execution,
streaming, bulk insert/update and bulk delete operations:

* ``pre_execute`` / ``post_execute``;
* ``pre_stream`` / ``post_stream``;
* ``pre_bulk_create`` / ``post_bulk_create``;
* ``pre_bulk_update`` / ``post_bulk_update``;
* ``pre_bulk_delete`` / ``post_bulk_delete``.

.. code-block:: python

   from async_orm_kit.observers.queryset_observer import BaseQuerySetObserver

   class AuditObserver(BaseQuerySetObserver):
       # Override only the hooks relevant to this application.
       pass

   query = User.objects.all(session).add_observer(AuditObserver())

``Manager.add_observer()`` attaches observers to every new QuerySet generated
by that manager. ``QuerySet.add_observer()`` returns a cloned QuerySet with
the observer attached. Register manager-level observers once during app setup.

Query hooks and processors
--------------------------

``Manager.add_query_hook(callback)`` transforms a freshly built queryset.
``Manager.add_result_processor(callback)`` raises ``NotImplementedError``.
It previously retained callbacks without executing them; use observers or
explicit result transformations instead.
These are application-controlled execution points; they should not accept
untrusted user-defined Python code. A query hook intended to enforce
security boundaries needs a separate policy review and authorization tests.

SQL diagnostics
---------------

For execution timings and probable N+1 patterns prefer
:class:`async_orm_kit.QueryProfiler`, which uses actual SQLAlchemy events.
Observer hooks alone do not measure driver cursor execution accurately.
