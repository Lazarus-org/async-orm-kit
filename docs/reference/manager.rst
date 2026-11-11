Manager reference
=================

.. autoclass:: async_orm_kit.Manager
   :members: clone_for_model, contribute_to_class, get_queryset, add_observer, add_query_hook, add_result_processor, using, all, filter, exclude, order_by, limit, offset, select_for_update, select_related, prefetch_related, annotate, values, values_list, distinct, defer, only, get, first, aggregate, count, exists, create, bulk_create, get_or_create, update_or_create, bulk_update, sql, explain, in_bulk, earliest, latest

Manager method calls require a first ``session`` parameter. The class-level
manager descriptor binds to a mapped model. ``using(session)`` returns a
session-bound proxy for fewer repeated arguments.

.. code-block:: python

   # Both patterns use the same caller-owned AsyncSession:
   users = await User.objects.filter(session, active=True)
   users = await User.objects.using(session).filter(active=True)

``use_cache`` and ``cache_ttl`` are reserved constructor options and should
not be assumed to enable a production cache.
