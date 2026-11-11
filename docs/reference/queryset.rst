QuerySet and compound-query reference
=====================================

.. autoclass:: async_orm_kit.QuerySet
   :members: filter, exclude, order_by, limit, offset, union, intersection, difference, cte, select_related, select_for_update, prefetch_related, annotate, having, all, first, get, aggregate, count, exists, values, values_list, distinct, with_lookup, with_lookups, defer, seek_after, seek_before, only, create, bulk_create, update, delete, sql, explain, stream, add_observer

``await queryset`` retrieves and caches all results on that QuerySet;
``async for ... in queryset`` and ``queryset.stream()`` stream rows while
the session remains open. QuerySet mutations are immutable clones;
QuerySet *write operations* execute within the caller's transaction.

.. autoclass:: async_orm_kit.CompoundQuerySet
   :members: filter, exclude, order_by, limit, offset, sql, cte, subquery, first, count, exists, stream

Compound QuerySets are returned by ``union()``, ``intersection()`` and
``difference()``. They support compound post-filtering only on projected
columns, before compound ordering/pagination. See :doc:`../advanced/sets-cte`.

.. autoclass:: async_orm_kit.QueryCompiler
   :members: compile

``QueryCompiler`` is useful for introspection and integration authors. Most
applications should use ``QuerySet.sql()`` or SQLAlchemy's statement compiler
rather than reaching into QuerySet state directly.
