Row locking and concurrency controls
====================================

``select_for_update()`` asks the database to lock selected rows for the
duration of a caller-owned transaction.

.. code-block:: python

   async with SessionFactory() as session:
       async with session.begin():
           users = await (
               User.objects.filter(session, active=True)
               .select_for_update(nowait=True)
           )
           # Safely perform work requiring these row locks here.

Keyword options include ``nowait``, ``skip_locked``, ``read``,
``key_share`` and ``of``. Supported SQL and behavior depend on the dialect
and server version. A flag that compiles does not establish runtime support.

SQLite does not support row-level ``FOR UPDATE`` semantics and is explicitly
rejected rather than silently treated as locked. PostgreSQL and MySQL also
have different locking syntaxes and capabilities.

**Do not mix row locks with keyset pagination or compound QuerySet branches.**
Set operation and ordering restrictions prevent several invalid or ambiguous
lock statements. Keep transactions short and design lock acquisition order to
reduce deadlocks. Use database isolation and retries deliberately in your
application layer; the library does not install an automatic retry policy.
