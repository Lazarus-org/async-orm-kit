QuerySets: filtering, results and writes
========================================

:class:`~async_orm_kit.QuerySet` is a lazy, session-bound, immutable query
builder. Operations such as ``filter()`` and ``order_by()`` return new
QuerySets and do not issue SQL until they are evaluated.

Filtering and ordering
----------------------

.. code-block:: python

   from async_orm_kit import Q

   base = User.objects.filter(session, active=True)
   selected = (
       base.filter(Q(role="admin") | Q(role="editor"))
       .exclude(email__isnull=True)
       .order_by("-created_at", "id")
   )
   users = await selected.limit(25)

Common scalar lookups include ``exact``, ``gt``, ``gte``, ``lt``, ``lte``,
``in``, ``range``, ``contains``, ``icontains``, ``startswith``,
``istartswith``, ``endswith``, ``iendswith`` and ``isnull``. Date/time
lookups include ``year``, ``month``, ``day``, ``hour``, ``minute`` and
``second`` for exact comparisons. See JSON-specific syntax in
:doc:`../advanced/json-array`.

Nested relationships use double underscores, for example
``posts__title__icontains="python"``. A negated to-many relationship
predicate is evaluated through correlated existence logic, so it excludes
a parent when **any** related row matches.

Result shapes
-------------

.. code-block:: python

   objects = await User.objects.all(session)
   dicts = await User.objects.values(session, "id", "email")
   pairs = await User.objects.values_list(session, "id", "email")
   ids = await User.objects.values_list(session, "id", flat=True)

``values()`` returns dictionaries, ``values_list()`` returns tuples, and
``values_list(flat=True)`` returns scalar values for a single field. Use
``distinct()`` to remove duplicates, and ``only()`` or ``defer()`` to load
fewer scalar columns. In async code, deferred fields must be explicitly
refreshed before access. See :doc:`../advanced/extension`.

Evaluating a query
------------------

.. code-block:: python

   rows = await User.objects.filter(session, active=True)
   one = await User.objects.filter(session, id=1).first()
   count = await User.objects.filter(session, active=True).count()
   exists = await User.objects.filter(session, id=1).exists()
   single = await User.objects.filter(session, email="a@example.com").get()

``get()`` raises ``ObjectDoesNotExist`` / ``MultipleObjectsReturned``
(and model-specific variants) when zero or several rows match.

For long result sets:

.. code-block:: python

   query = User.objects.filter(session, active=True).order_by("id")
   async for user in query.stream():
       process(user)

Keep the ``AsyncSession`` open for the entire stream. ``async for user in
query`` also streams; ``await query`` materializes rows and caches that
QuerySet's result. ``limit()`` and ``offset()`` support pagination. Integer
indexing and ``[:n]`` slices return a new QuerySet, not a row, and slicing
constrains subsequent chaining. Integer indexing evaluates to a list containing
zero or one result. Existing limits and offsets are respected when slicing.

Write operations
----------------

.. code-block:: python

   from async_orm_kit import F

   async with session.begin():
       new_user = await User.objects.create(session, email="new@example.com")
       changed = await User.objects.filter(session, active=True).update(
           login_count=F("login_count") + 1
       )
       removed = await User.objects.filter(session, active=False).delete()

``bulk_create()`` supports conflict policies and an optional ``returning``
mode; return behavior depends on the dialect and conflict mode.
``bulk_update()`` is exposed through the manager. Bulk update/delete reject
limits, offsets, grouping, DISTINCT, and joined relationship filters rather than
discarding those constraints. Select primary keys first for a restricted write. See
:doc:`../integrations-rst/mysql` for MySQL-specific limitations.

.. warning::

   QuerySet writes, even when flushed, are not committed until the caller
   commits. Never assume ``create()``, ``update()`` or ``delete()`` is an
   independent transaction.

SQL inspection
--------------

``query.sql()`` compiles SQL for inspection without executing. This SQL is
parameterized: do not log SQL with sensitive bind parameters. ``await
query.explain()`` runs a database-specific plan query; ``analyze=True`` may
**execute** the underlying SQL and should not be used casually on live data.
