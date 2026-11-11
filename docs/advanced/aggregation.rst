Aggregates and grouped predicates
=================================

Built-in aggregates are ``Count``, ``Sum``, ``Avg``, ``Min`` and ``Max``.
They resolve mapped columns or certain relationships and may include
``distinct=True``, ``filter=Q(...)``, ``default=...`` and ``output_field=...``
where appropriate. Read aggregate-specific signatures in the API reference.

Annotated results
-----------------

.. code-block:: python

   from async_orm_kit import Count, Q, Sum

   authors = await (
       Author.objects.annotate(session, total=Count("posts"))
       .filter(total__gte=2)
       .order_by("-total")
   )

``Count("posts")`` counts related primary-key values and preserves authors
with no related posts. ``Count(F("score"))`` counts only non-NULL column
values, not ``COUNT(*)``. With joined to-many relations, use
``distinct=True`` when duplicate joined rows would inflate an aggregate.

Grouped values
--------------

.. code-block:: python

   totals = await (
       Order.objects.values(session, "customer_id")
       .annotate(total=Sum("amount"), orders=Count())
       .having(total__gte=100)
       .order_by("customer_id")
   )

``having()`` expects predicates that refer to aggregate results. Ordinary
``filter()`` conditions are routed into ``WHERE``; aggregate alias
predicates are routed into ``HAVING``.

Mixed aggregate and row predicates
----------------------------------

The implementation handles complex boolean trees mixing aggregate
aliases and ordinary row filters:

.. code-block:: python

   from async_orm_kit import Q

   matched = await (
       Author.objects.annotate(session, total=Count("posts"))
       .filter(Q(total__gt=5) | Q(name="Ada"))
   )

To preserve group totals, the row-level side of such a mixed expression uses
correlated ``EXISTS`` against the root primary-key identity. The engine also
supports grouped NOT and separable mixed AND. Mixed correlated expressions
may produce heavier SQL; review plans on large datasets.

Conditional aggregates
----------------------

.. code-block:: python

   published = Author.objects.annotate(
       session,
       published_posts=Count("posts", filter=Q(posts__published=True)),
   )

Filtered aggregation uses ``CASE WHEN ... THEN ... ELSE NULL END`` internally,
providing a portable expression rather than relying on database-specific
aggregate ``FILTER`` support. ``default`` uses ``COALESCE`` and
``output_field`` accepts SQLAlchemy type objects.

Terminal summaries
------------------

.. code-block:: python

   summary = await Order.objects.filter(session, is_paid=True).aggregate(
       total=Sum("amount", default=0),
       count=Count(),
   )

``aggregate()`` returns a dictionary. It intentionally rejects DISTINCT,
grouped/annotated, projected, or sliced QuerySets whose interpretation would
otherwise be ambiguous. Use ``values(...).annotate(...)`` for grouped data.

Custom aggregates use :class:`async_orm_kit.AggregateFunction`. See
:doc:`extension`.
