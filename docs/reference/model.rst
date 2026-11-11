Model, metadata and configuration reference
===========================================

.. autoclass:: async_orm_kit.Model
   :members: get_manager, get_default_manager, get_all_managers, to_dict, from_dict, update_from_dict, get_field_names, get_primary_keys, get_primary_key_names, get_primary_key_values, refresh_from_db, save, delete, clean, full_clean

.. autoclass:: async_orm_kit.models.meta.Base

.. autoclass:: async_orm_kit.models.meta.ModelMeta

.. autoclass:: async_orm_kit.ORMConfig
   :members:
   :undoc-members:

``DEFAULT_CONFIG`` is a frozen instance of ``ORMConfig`` with
``max_joinedload_depth=2``. Application code can pass independent config
objects to different managers rather than relying on mutable globals.

.. code-block:: python

   from async_orm_kit import Manager, ORMConfig

   objects = Manager(config=ORMConfig(max_joinedload_depth=3))
