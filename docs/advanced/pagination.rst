Offset, keyset and cursor pagination
====================================

Offset-based pagination
-----------------------

.. code-block:: python

   page = await User.objects.order_by(session, "id").limit(20).offset(40)

Offset pagination is easy to implement but can be slow for deep pages and
can shift under concurrent inserts/deletes. Always specify a stable ordering.

Keyset pagination
-----------------

``seek_after()`` and ``seek_before()`` construct a lexicographic predicate
against an ordered QuerySet, with bind parameters for cursor values.

.. code-block:: python

   base = Item.objects.order_by(session, "-created_at", "id")
   first_page = await base.limit(20)

   if first_page:
       last = first_page[-1]
       second_page = await (
           base.seek_after(last.created_at, last.id).limit(20)
       )

The ordering must use root-model, scalar, **non-nullable** columns and include
all primary-key fields as deterministic tie-breakers. Mixed ascending and
descending directions are supported. One cursor may be applied to a QuerySet.

Unsupported keyset shapes include OFFSET, DISTINCT, grouping, aggregation,
row locks, annotation ordering and relationship collection JOINs that would
multiply result rows. Invalid cursor values are rejected.

``seek_before()`` computes earlier rows in the same declared sort order;
applications control their display order and reverse navigation UI.

Public cursor tokens
--------------------

The API takes decoded Python values, **not** an opaque cursor string. For a
public API, serialize the ordered field values and protect the token against
tampering at the application layer. Include a query/filter version in the
token when necessary; validate type and ordering on decode. Cursor pagination
is not a snapshot: concurrent updates can affect subsequent pages.
