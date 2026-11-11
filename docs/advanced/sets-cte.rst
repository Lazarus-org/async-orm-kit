Set operations and common table expressions
============================================

Set operations
--------------

``union()``, ``intersection()`` and ``difference()`` combine compatible
QuerySets bound to the **same SQLAlchemy session**. They produce
:class:`~async_orm_kit.CompoundQuerySet`.

.. code-block:: python

   admins = User.objects.filter(session, role="admin").values("id", "email")
   editors = User.objects.filter(session, role="editor").values("id", "email")

   everyone = admins.union(editors)            # UNION (deduplicates)
   all_rows = admins.union(editors, all=True)   # UNION ALL
   both = admins.intersection(editors)         # INTERSECT
   only_admins = admins.difference(editors)    # EXCEPT

Each branch must have compatible projection names, column counts and known
result types. Model branches must map the same model. Unsupported branch
orderings, row locks, projections with extra annotations and eager loading
are rejected rather than being changed silently.

Filtering the combined result
-----------------------------

The API supports ``filter()`` and ``exclude()`` over projected
result columns:

.. code-block:: python

   rows = await (
       admins.union(editors)
       .filter(email__icontains="@example.com")
       .exclude(id=5)
       .order_by("id")
       .limit(20)
   )

The compound filters accept ``Q`` conditions and basic lookup kwargs.
They cannot traverse relationships or reference columns not projected by
both branches. Apply filters **before** ordering and pagination of the
compound result. Filtering after ``order_by()``, ``limit()`` or ``offset()``
is rejected. The result supports ``await``, ``async for``, ``stream()``,
``first()``, ``count()`` and ``exists()``.

SQLAlchemy interop
------------------

``CompoundQuerySet.cte()`` and ``CompoundQuerySet.subquery()`` expose
SQLAlchemy objects. ``QuerySet.cte(name=None, recursive=False)`` does too.
Use them with regular SQLAlchemy statements when deeper joins are required.

.. code-block:: python

   from sqlalchemy import select

   ids = User.objects.filter(session, active=True).values_list("id", flat=True)
   active_ids = ids.cte("active_ids")
   result = await session.execute(select(active_ids.c.id))

Recursive CTEs
--------------

Build the recursive term through SQLAlchemy's CTE API:

.. code-block:: python

   from sqlalchemy import select

   seed = Node.objects.filter(session, id=1).values_list("id", flat=True)
   tree = seed.cte("tree", recursive=True)
   tree = tree.union_all(
       select(Node.id).join(tree, Node.parent_id == tree.c.id)
   )
   result = await session.execute(select(tree.c.id))

Ensure recursive queries terminate and guard against cycles in user-defined
graphs. Dialect and server-version support differs, particularly for older
MySQL installations and some EXCEPT/INTERSECT forms.
