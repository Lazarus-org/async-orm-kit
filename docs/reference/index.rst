Public API reference
====================

This reference follows the actual imports exposed by
``async_orm_kit.__init__`` at version ``1.0.0``. The module paths
below can also be imported directly for integrations and advanced APIs.

.. toctree::
   :maxdepth: 2

   model
   manager
   queryset
   expressions
   fields
   integrations
   errors

See :doc:`../quickstart` and the guides for **correct session-bound usage**.
Some historical in-source docstring examples predate mandatory explicit
session arguments; in those cases, the examples in this documentation site
and the method signatures take precedence.
