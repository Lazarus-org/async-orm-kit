# FileField integration with async-storage-kit

Async ORM Kit stores a storage key in the mapped SQLAlchemy string column. The
file bytes remain in the configured storage backend. The adapter is optional and
uses the public `async-storage-kit` contract; the ORM core does not import or
require that package at runtime.

## Install

```bash
pip install "async-orm-kit[storage]"
```

The `storage` extra installs `async-storage-kit` 1.x. Filesystem and memory
backends need no additional storage-provider extra. For S3, install the storage
package's S3 extra as well:

```bash
pip install "async-orm-kit[storage]" "async-storage-kit[s3]"
```

## Configure a backend

```python
from async_storage_kit import FileSystemStorage, LazyStorageProxy, StorageRegistry
from async_orm_kit import AsyncStorageKitAdapter

registry = StorageRegistry({
    "default": FileSystemStorage("./media", base_url="/media/"),
})
file_storage = AsyncStorageKitAdapter(
    LazyStorageProxy(registry, "default"),
    on_conflict="rename",
)
```

The adapter accepts a configured backend instance or the package's
`LazyStorageProxy`. The proxy is not resolved until the first storage operation.
The registry and backend should be application-scoped; the adapter does not
create, close, or otherwise own them.

## Declare and upload

```python
from sqlalchemy.orm import Mapped, mapped_column
from async_orm_kit import FileField, Model

class Document(Model):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    attachment: Mapped[str | None] = mapped_column(
        FileField(
            storage=file_storage,
            upload_to="documents/%Y/%m",
            max_length=255,
        ),
        nullable=True,
    )

async def create_document(session, content):
    document = Document()
    stored_name = await document.attachment.save("report.pdf", content)
    session.add(document)
    await session.commit()
    return document, stored_name
```

`FieldFile.save(name, content)` and `FieldFile.store(name, content)` are aliases.
They validate the supplied content, generate a relative POSIX storage key, upload
the object, and update the mapped string attribute. They do **not** add, flush,
or commit the model; the caller owns the SQLAlchemy transaction.

## Read and stream

```python
content = await document.attachment.read()  # Buffers the complete object.

async for chunk in document.attachment.iter_chunks(chunk_size=256 * 1024):
    await send_to_client(chunk)

# Compatibility API; this also buffers the complete object.
file_obj = await document.attachment.open("rb")
try:
    data = file_obj.read()
finally:
    document.attachment.close()
```

Prefer `iter_chunks()` for large files. The adapter's `open()` is backed by
`async-storage-kit.read()` and therefore materializes the whole object in a
`BytesIO`. It supports `rb` mode only; it is not a writable storage handle.

## Metadata and backend capabilities

```python
size = await document.attachment.size()
metadata = await document.attachment.metadata()
modified_at = await document.attachment.get_modified_time()

# Filesystem capability (not available for MemoryStorage/S3Storage).
local_path = document.attachment.path
public_url = document.attachment.url

# S3 capability (requires async-storage-kit[s3] and a configured S3Storage).
signed_url = await document.attachment.get_presigned_url(expire=900)
```

`path`, `url`, and `get_presigned_url` are capability-dependent. The adapter
forwards the backend's `UnsupportedOperation` errors rather than inventing a
local path or public URL. `get_accessed_time()` and `get_created_time()` are
available only when the concrete backend supports them. `metadata()` returns
the backend's metadata object, including the UTC `modified_at` timestamp.

## Collision policy

The adapter accepts `on_conflict="rename"` (default), `"overwrite"`, or
`"error"`. The backend enforces the policy atomically where supported. The
returned storage key may differ from the requested name when rename policy
allocates an alternative name.

```python
storage = AsyncStorageKitAdapter(
    FileSystemStorage("./media"),
    on_conflict="error",
)
```

## Transaction and cleanup boundaries

Storage operations are not part of the SQL database transaction:

- Upload first, then persist the returned key with the caller's session.
- If the DB transaction fails after upload, delete the newly uploaded object as
  application-level compensation.
- Replacing a key does not delete the old object automatically. Delete the old
  object only after the database update commits, otherwise a rollback could leave
  the DB pointing to a missing object.
- `FieldFile.delete()` deletes the object immediately and clears the in-memory
  mapped key; the caller must commit the DB transaction. If storage deletion
  fails, the exception propagates and the mapped key is retained for retry.
- For rollback-safe deletion, clear the DB reference and commit first, then delete
  the old object through the configured adapter/backend:

  ```python
  old_name = document.attachment.name
  document.attachment = None
  await session.commit()
  if old_name is not None:
      await file_storage.delete(old_name)
  ```

- No storage object is automatically deleted by bulk SQL DELETE operations.
  Applications that need cascading object cleanup must implement it explicitly.

The adapter does not provide authentication, authorization, malware scanning,
quotas, backups, or HTTP delivery. Those remain application responsibilities.

## Optional dependency boundary

`async-storage-kit` is declared only in the `storage` and contributor `test`
extras. Importing `async_orm_kit`, including `AsyncStorageKitAdapter`, does not
import `async_storage_kit`. Applications that do not use this integration do not
need to install it.
