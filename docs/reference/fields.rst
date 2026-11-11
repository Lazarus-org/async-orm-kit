Field and storage API reference
===============================

.. autoclass:: async_orm_kit.StringField
   :members: load_dialect_impl

.. autoclass:: async_orm_kit.FileField
   :members: generate_filename, clean

.. autoclass:: async_orm_kit.FieldFile
   :members: name, path, url, get_presigned_url, size, open, read, iter_chunks, metadata, close, store, save, delete, exists, get_accessed_time, get_created_time, get_modified_time

.. autoclass:: async_orm_kit.AsyncStorageKitAdapter
   :members: save, open, read, iter_chunks, delete, exists, size, generate_filename, path, url, get_presigned_url, get_modified_time, get_accessed_time, get_created_time, metadata

See :doc:`errors` for ``FieldValidationError``.

Storage backend protocol is documented by
``async_orm_kit.models.fields.typed.file.StorageBackend``. Refer to
:doc:`../integrations-rst/storage` for the transaction and lifecycle contract.
