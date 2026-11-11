JSON, JSONB and PostgreSQL ARRAY
================================

JSON paths (SQLite, PostgreSQL, MySQL)
--------------------------------------

Mapped ``sqlalchemy.JSON`` (or PostgreSQL ``JSONB``) columns support nested
key and index lookups using double underscores:

.. code-block:: python

   rows = await Event.objects.filter(
       session,
       payload__user__age__gte=18,
       payload__values__0__name="first",
       payload__user__active=True,
   )

Paths are compiled using SQLAlchemy's JSON index operators and bound values.
Numeric-looking nonnegative segments are interpreted as array indexes.
For an object key whose literal name consists only of digits, use an
explicit ``JSONPath`` expression.

Explicit JSONPath
-----------------

.. code-block:: python

   from async_orm_kit import F, JSONPath

   events = await Event.objects.annotate(
       session,
       score=JSONPath(F("payload"), "metrics", "score", as_type="integer"),
   ).filter(score__gte=10)

``as_type`` accepts ``"json"`` (the default), ``"string"``, ``"integer"``,
``"float"`` and ``"boolean"``. Use an explicit scalar type for comparisons
that must not use JSON-text equality. JSON NULL, SQL NULL and missing keys
may differ across databases. ``__isnull`` checks the extracted scalar and
can include missing paths.

PostgreSQL JSONB operations
---------------------------

.. code-block:: python

   from async_orm_kit import JSONContains, JSONHasKey, F

   events = await Event.objects.filter(
       session,
       JSONContains(F("payload"), {"enabled": True}),
       JSONHasKey(F("payload"), "tenant"),
   )

The mapped column must use ``sqlalchemy.dialects.postgresql.JSONB``.
Django-style root JSONB lookups also include ``payload__contains={...}``,
``payload__has_key="key"``, ``payload__has_keys=[...]`` and
``payload__has_any_keys=[...]``. These operators are PostgreSQL-only:
they are not emulated as unsafe string matching on SQLite/MySQL. Avoid
using string ``icontains`` on a root JSON object; choose a string path.

PostgreSQL ARRAY operations
---------------------------

.. code-block:: python

   from async_orm_kit import ArrayContains, ArrayOverlap, ArrayIndex, ArrayLength, F

   matching = await Event.objects.filter(
       session, ArrayOverlap(F("tags"), ["python", "sql"])
   )
   projected = await Event.objects.annotate(
       session,
       first_tag=ArrayIndex(F("tags"), 1),
       tag_count=ArrayLength(F("tags")),
   )

``ArrayContains`` and ``ArrayOverlap`` apply to mapped PostgreSQL ``ARRAY``
columns. Standard PostgreSQL arrays are 1-indexed; respect SQLAlchemy's
``zero_indexes=True`` convention if it is configured for the mapped type.
``ArrayLength`` accepts a ``dimension`` argument, default 1.

Typed SQL remains dialect-dependent
-----------------------------------

JSON scalar conversion, key/path null semantics, ARRAY equality and index
behavior must be verified on the actual database server and driver. The
current package has offline compilation tests, but live PostgreSQL/MySQL
advanced-query test execution remains a release gate.
