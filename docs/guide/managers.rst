Managers and custom QuerySets
=============================

Every concrete :class:`~async_orm_kit.Model` has a manager, normally
``objects``. Manager methods require the active ``AsyncSession`` **as their
first argument**. QuerySet methods use the bound session and do not require it
again.

.. code-block:: python

   query = User.objects.filter(session, active=True).order_by("email")
   users = await query
   first = await User.objects.first(session)
   found = await User.objects.get(session, id=1)

Manager conveniences include ``all``, ``filter``, ``exclude``, ``get``,
``first``, ``aggregate``, ``count``, ``exists``, ``create``,
``bulk_create``, ``get_or_create``, ``update_or_create``, ``bulk_update``,
``in_bulk``, ``earliest`` and ``latest``. They delegate to the queryset
or perform their own work under the caller-owned session.

Bound managers
--------------

.. code-block:: python

   async with SessionFactory() as session:
       bound = User.objects.using(session)
       records = await bound.filter(active=True).order_by("id")

``using()`` creates a proxy bound to one session; it does not open or close
that session.

Custom managers
---------------

.. code-block:: python

   from async_orm_kit import Manager

   class ActiveUserManager(Manager):
       def get_queryset(self, session):
           return super().get_queryset(session).filter(active=True)

   class Account(Model):
       __tablename__ = "accounts"
       # ... normal SQLAlchemy mapped fields ...
       active_accounts = ActiveUserManager()

Or pass a custom ``QuerySet`` subclass through
``Manager(queryset_class=MyQuerySet)``. QuerySet clone operations preserve the
subclass type, but application-specific additional mutable state may require
extra care when copying.

``add_query_hook()`` transforms each newly created QuerySet; useful for
application policies, although tenant isolation must be designed carefully.
``add_observer()`` allows callbacks around query execution.
``add_result_processor()`` is an unimplemented legacy placeholder and raises
``NotImplementedError``; transform fetched results explicitly. Hook registration mutates the manager object, so register hooks at
startup, not per request. See :doc:`observers`.

The constructor's ``use_cache`` and ``cache_ttl`` arguments are reserved
rather than a completed application caching layer. Do not rely on them for
cross-request result caching.
