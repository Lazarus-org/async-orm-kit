FileField and async-storage-kit
===============================

The ORM stores **relative object keys** in mapped ``FileField`` columns.
Object bytes belong to an explicitly configured asynchronous storage backend.
``async-storage-kit`` is an optional extra, not a core dependency.

.. code-block:: console

   python -m pip install "async-orm-kit[storage]"

Declare the storage adapter
---------------------------

.. code-block:: python

   from sqlalchemy.orm import Mapped, mapped_column
   from async_storage_kit import FileSystemStorage
   from async_orm_kit import AsyncStorageKitAdapter, FileField, Model

   storage = AsyncStorageKitAdapter(
       FileSystemStorage("./media", base_url="/media/"),
       on_conflict="rename",
   )

   class Document(Model):
       __tablename__ = "documents"
       id: Mapped[int] = mapped_column(primary_key=True)
       attachment: Mapped[str | None] = mapped_column(
           FileField(storage=storage, upload_to="documents/%Y/%m"),
           nullable=True,
       )

Upload and read
---------------

The mapped ``attachment`` descriptor exposes ``FieldFile`` methods on an
instance. Uploading changes the mapped filename but **does not commit**:

.. code-block:: python

   async with SessionFactory() as session:
       document = Document()
       uploaded_key = await document.attachment.save("report.pdf", content)
       async with session.begin():
           session.add(document)

``content`` must be accepted by the configured backend (for example, a
file-like object as defined by its storage contract). The adapter supports
``read()``, ``iter_chunks()``, ``delete()``, ``exists()``, ``size()``,
``metadata()`` and timestamp accessors. ``open()`` may buffer full content;
use ``iter_chunks()`` for bounded-memory response streams.

``url`` and ``path`` are backend-dependent: a cloud key may have no local
filesystem path or public URL. For private objects, prefer a backend-supported
``get_presigned_url()`` with an appropriate short expiration.

Cross-system consistency
------------------------

.. warning::

   Database and object storage transactions are **not atomic together**.
   An uploaded object may need compensating deletion if database persistence
   fails. Deleting or replacing a stored object before a SQL commit can leave
   a DB row referencing missing bytes. Delete old objects only after a
   successful application-managed commit.

File names are normalized and unsafe traversal patterns rejected. Do not
expose file keys as unrestricted filesystem paths, and always enforce
application authorization before returning private files or signed URLs.
