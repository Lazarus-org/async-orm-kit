Expressions, ``Q`` and SQL functions
====================================

Expressions are resolved to SQLAlchemy expressions before SQL compilation.
They remain parameterized and may be used in annotations, filters and some
updates.

Boolean predicates with Q
-------------------------

.. code-block:: python

   from async_orm_kit import Q

   query = User.objects.filter(
       session,
       Q(role="admin") | (Q(role="editor") & ~Q(active=False)),
   )

``Q`` supports ``&``, ``|`` and ``~``. Multiple arguments and keyword
predicates are combined as AND. The documented NULL-negation policy retains
UNKNOWN rows when excluding a predicate that is not true. For relationships,
negation uses correlated existence rather than per-joined-row inequality.

Field references with F
-----------------------

.. code-block:: python

   from async_orm_kit import F, Value

   query = Product.objects.annotate(
       session,
       total=F("unit_price") * F("quantity"),
       label=Value("computed"),
   )

``F`` supports numeric/arithmetic composition and additional portable
floor-division and bitwise operators. ``F.eq()`` and ``F.ne()`` create
SQL comparison expressions. ``Value`` preserves Python structural equality
for ``==``; use ``Value(...).eq(...)`` when you intend SQL equality.

Atomic database updates
-----------------------

.. code-block:: python

   from async_orm_kit import Case, F, Value, When

   async with session.begin():
       await Product.objects.filter(session, id=7).update(
           quantity=F("quantity") + 1,
       )
       await Product.objects.filter(session, id=7).update(
           quantity=Case(
               When(quantity__gte=10, then=F("quantity") + 2),
               default=Value(0),
           ),
       )

The package rejects aggregate/window expressions in an UPDATE and joined
relationship UPDATE shapes it cannot safely compile. SQLAlchemy transactions
are still caller-owned.

SQL functions
-------------

.. code-block:: python

   from sqlalchemy import String
   from async_orm_kit import (
       Abs, Cast, Coalesce, Concat, Extract, F, Length, Lower,
       NullIf, Round, Trim, Upper, Value,
   )

   query = Product.objects.annotate(
       session,
       normalized=Lower(Trim(F("title"))),
       title_length=Length(F("title")),
       display=Concat(Upper(F("title")), Value("!")),
       amount=Coalesce(F("discount"), Value(0)),
       amount_text=Cast(F("unit_price"), String()),
   )

``Extract`` validates date part names against a fixed allowlist. Also
supported: ``Abs``, ``Round`` and ``NullIf``. JSONPath has its own typed
extraction handling; see :doc:`json-array`.

Window expressions
------------------

.. code-block:: python

   from async_orm_kit import DenseRank, F, Lag, RowNumber, Window

   query = Product.objects.annotate(
       session,
       row_number=Window(RowNumber(), order_by=[F("id").asc()]),
       rank=Window(DenseRank(), order_by=[F("unit_price").desc()]),
       previous=Window(Lag(F("unit_price")), order_by=[F("id").asc()]),
   )

``Rank`` and ``Lead`` are also supported. ``Window(..., partition_by=[...],
order_by=[...])`` wraps an expression in SQL ``OVER``. Window aliases cannot
be filtered directly in a ``WHERE``/``HAVING`` on the same SELECT. Select a
subquery or use native SQLAlchemy expressions where that layer is needed.

See :doc:`aggregation` and :doc:`subqueries` for aggregate and correlated
expression semantics.
