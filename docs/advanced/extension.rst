Custom lookups, functions, aggregates and deferred columns
===========================================================

Register custom lookups locally
-------------------------------

.. code-block:: python

   from async_orm_kit import Model

   def multiple_of(column, divisor):
       return column % divisor == 0

   query = (
       Item.objects.all(session)
       .with_lookup("multiple_of", multiple_of)
       .filter(score__multiple_of=5)
   )
   results = await query

``with_lookup(name, handler)`` requires a lowercase identifier and a trusted
callable returning a Boolean-typed SQLAlchemy expression.
``with_lookups(foo=foo, bar=bar)`` registers several handlers. The registry
belongs to the QuerySet clone; it does not mutate a process-wide lookup
table. Built-in lookups cannot be overridden by custom registrations.

A handler is application-owned executable Python, **not** a safe user-supplied
plugin definition. Always use SQLAlchemy bound parameters, never SQL assembled
from arbitrary user text.

Custom SQL functions
--------------------

.. code-block:: python

   from sqlalchemy import func
   from async_orm_kit import F, Function

   query = Item.objects.annotate(
       session,
       initial=Function(lambda name: func.substr(name, 1, 1), F("name")),
   )

``Function(factory, *operands)`` resolves ORM operands and invokes the trusted
SQLAlchemy factory. The factory must return a SQLAlchemy expression, not a raw
SQL string. Nonportable functions require their own database-specific tests.

Custom aggregate functions
--------------------------

.. code-block:: python

   from sqlalchemy import func
   from async_orm_kit import AggregateFunction

   query = (
       Item.objects.values(session, "category")
       .annotate(total=AggregateFunction("price", lambda col: func.sum(col)))
   )

``AggregateFunction`` shares the built-in aggregate plumbing, including
``distinct``, ``filter``, ``default`` and ``output_field`` where applicable.
Custom SQLAlchemy factory results must be genuine aggregate functions.

Deferred columns
----------------

.. code-block:: python

   item = await Item.objects.defer(session, "description").get(id=1)
   await session.refresh(item, ["description"])
   print(item.description)

``defer()`` accepts root scalar fields, not relationships or primary keys.
``only()`` limits the initially selected scalar columns; these modes are
incompatible with ``values()``/``values_list()`` projections and have
restrictions on their combinations. Accessing a deferred column without
explicitly loading it in an ``AsyncSession`` can raise ``MissingGreenlet``.

These extension points are *not* a promise of a global custom field/lookup
registry or a general SQL plugin language.
