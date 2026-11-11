Relationships and loading strategies
====================================

Relations are declared using normal SQLAlchemy ``relationship()`` and
``ForeignKey`` mapping APIs. Async ORM Kit adds relationship-aware field
navigation and automatic query planning above those mappings.

.. code-block:: python

   authors = await Author.objects.filter(
       session, posts__title__icontains="orm"
   )

Join and prefetch strategies
----------------------------

``select_related()`` is designed for joined eager loading of scalar
relationships. ``prefetch_related()`` uses separate SELECTs where appropriate,
especially for collections. Both accept nested relationship paths.

.. code-block:: python

   posts = await (
       Post.objects.filter(session, author__name__icontains="ada")
       .select_related("author")
   )

   authors = await (
       Author.objects.all(session).prefetch_related("posts")
   )

The planner analyzes relationship paths and can demote deep joins to
select-in loading. Use ``ORMConfig(max_joinedload_depth=...)`` to adjust the
policy. Exact query counts depend on relationships and SQLAlchemy loading
behavior; inspect real SQL for performance-critical endpoints.

Negated collection predicates
-----------------------------

.. code-block:: python

   # Exclude every author having at least one post titled "spam".
   clean = await Author.objects.exclude(session, posts__title="spam")

This is **not** implemented as merely ``posts.title != 'spam'`` on joined
rows, which would incorrectly retain authors who also have a non-spam post.
Nullable predicates and NULL comparisons need dialect-aware testing.

Relationship counts
-------------------

.. code-block:: python

   from async_orm_kit import Count

   authors = await (
       Author.objects.annotate(session, post_count=Count("posts"))
       .filter(post_count__gte=0)
   )

A to-many count targets related primary keys. Parents without children
remain in a zero-count result when the necessary outer join is used. Multi-hop
and composite-key relationships have additional restrictions; use explicit
columns for a composite related primary key when required.

N+1 query diagnostics
---------------------

Use :doc:`../advanced/observability` to measure actual SQL executions and
spot repeated lookup patterns. Results are heuristic: repeated queries are
not definitive proof of a particular ORM loading defect.
