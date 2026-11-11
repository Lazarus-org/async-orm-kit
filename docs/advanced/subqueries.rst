Correlated subqueries, OuterRef and Exists
==========================================

``Subquery(queryset)`` returns a scalar SQL subquery expression;
``Exists(queryset)`` produces a correlated existence predicate.
``OuterRef`` binds a field of the enclosing query. Subquery objects do not
create or own database sessions.

.. code-block:: python

   from async_orm_kit import Exists, OuterRef, Subquery

   recent_posts = (
       Post.objects.filter(session, author_id=OuterRef("id"))
       .order_by("-id")
       .values("title")
       .limit(1)
   )

   authors = await Author.objects.annotate(
       session, latest_title=Subquery(recent_posts)
   )

   matching_posts = Post.objects.filter(
       session, author_id=OuterRef("id"), title__icontains="python"
   )
   authors_with_posts = await Author.objects.filter(session, Exists(matching_posts))

Scalar subqueries must select **one column**. A model QuerySet with a simple
primary key can be converted to a one-column ``__in`` subquery; composite
primary keys require explicit projection.

Nested references
-----------------

``OuterRef(OuterRef("name"))`` reaches a grandparent query when nested
subqueries are correctly structured. References that cannot be resolved
outside a correlated query are rejected. For self-correlation, use a distinct
SQLAlchemy ``aliased(Model)`` entity for the inner query rather than relying
on ambiguous same-model identity.

Grouped correlated aggregates
-----------------------------

When an annotated grouped QuerySet is used as a scalar subquery, its
original grouping keys are preserved, including when the outer query selects
only the aggregate expression. Zero-count parents and outer joins have
specific regression coverage in the source tree.

Composite primary keys
----------------------

``pk=(tenant_id, record_id)`` and ``pk__in=[(...), (...)]`` work for
mapped composite primary keys. ``OuterRef("pk")`` correlates an entire
composite identity through tuple equality. A scalar ``Subquery`` is still
single-column and cannot return a composite identity directly.

When SQLAlchemy is a better fit
-------------------------------

Native SQLAlchemy ``select()``, ``exists()``, ``aliased()``, ``lateral()`` and
``scalar_subquery()`` remain available for cases where a QuerySet's
correlation restrictions are too narrow. Test compiled SQL and expected rows
on the actual target dialect.
