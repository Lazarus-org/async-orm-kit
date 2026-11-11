import io
import logging
import re
from collections.abc import AsyncIterator, Callable
from typing import IO, Any, Protocol, cast

from sqlalchemy.types import String

from async_orm_kit.models.fields.exceptions import FieldValidationError
from async_orm_kit.models.fields.typed.base import BaseValidatedType, ValidatorType

logger = logging.getLogger(__name__)


class StorageBackend(Protocol):
    """Structural contract required by the legacy async FileField adapter.

    A concrete backend is injected by the application. The ORM deliberately
    does not create or own a default storage instance.
    """

    async def save(
        self, name: str, content: Any, *, max_length: int | None = None
    ) -> str: ...
    async def open(self, name: str, mode: str = "rb") -> IO[bytes]: ...
    async def delete(self, name: str) -> None: ...
    async def exists(self, name: str) -> bool: ...
    async def size(self, name: str) -> int: ...
    def generate_filename(self, name: str) -> str: ...


def is_file_like(value: Any) -> bool:
    """
    Minimal heuristic for 'file-like':
    - Starlette UploadFile
    - Has .read and .name attributes
    Customize as needed for your app.
    """
    return isinstance(value, (io.IOBase, io.BufferedIOBase)) or (
        hasattr(value, "read") and callable(value.read) and hasattr(value, "name")
    )


class FieldFile:
    """
    Descriptor for file attributes on model instances.
    Provides Django-like file interface with lazy loading.
    """

    def __init__(self, instance, field: "FileField", name: str | None):
        self.instance = instance
        self.field = field
        self.storage = field.storage
        self._name = name
        self._file: Any = None
        self._committed = True

    def __str__(self):
        return self.name or ""

    def __bool__(self):
        return bool(self.name)

    def __eq__(self, other):
        if hasattr(other, "name"):
            return self.name == other.name
        return self.name == other

    def __hash__(self):
        return hash(self.name)

    def _require_file(self):
        """Raise ValueError if no file is associated with this field."""
        if not self:
            raise ValueError(
                f"The '{self.field.attname}' attribute has no file associated with it."
            )

    def _required_name(self) -> str:
        self._require_file()
        return cast(str, self.name)

    def _get_file(self):
        self._require_file()
        if self._file is None:
            raise RuntimeError(
                "FileField storage is asynchronous; call `await field.open()` first"
            )
        return self._file

    def _require_storage(self) -> StorageBackend:
        storage = self.storage
        if storage is None:
            raise RuntimeError(
                "FileField requires an explicitly configured storage backend. "
                "Pass `storage=` when declaring the field."
            )
        return storage

    def _set_file(self, file):
        self._file = file

    def _del_file(self):
        del self._file

    file = property(_get_file, _set_file, _del_file)

    @property
    def name(self) -> str | None:
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    @property
    def path(self) -> str:
        """Get local filesystem path (only for filesystem storage)"""
        self._require_file()
        storage = self._require_storage()
        path = getattr(storage, "path", None)
        if not callable(path):
            raise NotImplementedError("The configured storage has no local path")
        return path(self.name)

    @property
    def url(self) -> str:
        """Get public URL for the file"""
        self._require_file()
        storage = self._require_storage()
        url = getattr(storage, "url", None)
        if not callable(url):
            raise NotImplementedError(
                "The configured storage does not expose public URLs"
            )
        return url(self.name)

    async def get_presigned_url(self, expire: int = 3600) -> str:
        """Get temporary signed URL (for cloud storage)"""
        self._require_file()
        storage = self._require_storage()
        method = getattr(storage, "get_presigned_url", None)
        if not callable(method):
            raise NotImplementedError(
                "The configured storage has no presigned URL support"
            )
        return await method(self.name, expire=expire)

    async def size(self) -> int:
        """
        Get file size asynchronously.
        For uncommitted files, returns the size of the in-memory file object.
        For committed files, queries the storage backend.
        """
        self._require_file()
        if not self._committed:
            # Uncommitted file - get size from file object
            if self._file is not None:
                if hasattr(self._file, "size"):
                    return self._file.size
                # Fallback: seek to end to get size
                current_pos = self._file.tell()
                self._file.seek(0, 2)  # Seek to end
                size = self._file.tell()
                self._file.seek(current_pos)  # Restore position
                return size
            return 0
        # Committed file - get size from storage
        return await self._require_storage().size(self._required_name())

    async def open(self, mode: str = "rb") -> IO[bytes]:
        """
        Open the file from storage.
        If the file is already open, reopen it in the specified mode.
        """
        self._require_file()

        if self._file is None:
            self._file = await self._require_storage().open(self._required_name(), mode)
        else:
            # File exists but may be closed - reopen it
            if hasattr(self._file, "open"):
                self._file.open(mode)
            else:
                # File doesn't support reopening; release the old handle first.
                self.close()
                self._file = await self._require_storage().open(
                    self._required_name(), mode
                )

        return self._file

    async def read(self) -> bytes:
        """Read the complete stored object into memory.

        For large files, prefer :meth:`iter_chunks` to keep memory bounded.
        """
        self._require_file()
        method = getattr(self._require_storage(), "read", None)
        if not callable(method):
            file_obj = self._file
            if file_obj is None or getattr(file_obj, "closed", False):
                file_obj = await self.open("rb")
            current = file_obj.tell()
            try:
                file_obj.seek(0)
                return file_obj.read()
            finally:
                file_obj.seek(current)
        return await method(self.name)

    async def iter_chunks(self, chunk_size: int = 256 * 1024) -> AsyncIterator[bytes]:
        """Stream a stored object in bounded chunks when supported by storage."""
        self._require_file()
        method = getattr(self._require_storage(), "iter_chunks", None)
        if not callable(method):
            raise NotImplementedError(
                "The configured storage does not support streaming downloads"
            )
        iterator = method(self.name, chunk_size=chunk_size)
        try:
            async for chunk in iterator:
                yield chunk
        finally:
            close = getattr(iterator, "aclose", None)
            if callable(close):
                await close()

    async def metadata(self) -> Any:
        """Return backend metadata for this stored object."""
        self._require_file()
        method = getattr(self._require_storage(), "metadata", None)
        if not callable(method):
            raise NotImplementedError(
                "The configured storage has no metadata capability"
            )
        return await method(self.name)

    @property
    def closed(self) -> bool:
        """Check if the file is closed."""
        file = getattr(self, "_file", None)
        return file is None or getattr(file, "closed", True)

    def close(self):
        """Close the file if it's open."""
        file = getattr(self, "_file", None)
        if file is not None:
            file.close()

    def _set_instance_attribute(self, name: str | None) -> None:
        """Update the mapped string and retain this FieldFile in the cache."""
        if self.instance is None:
            return
        setattr(
            self.instance, self.field.attname, name
        ) if self.field.attname is not None else None
        cache_name = f"_{self.field.attname}_file"
        setattr(self.instance, cache_name, self)

    async def store(self, name: str, content: Any) -> str:
        """Upload content and update the mapped filename without a DB commit.

        Storage and database transactions are independent. The caller remains
        responsible for flushing/committing the model and compensating for an
        uploaded object if the database transaction later fails.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("A non-empty filename is required")
        if content is None:
            raise ValueError("File content must not be None")

        # Always run the field's sync and async validators through the same
        # public validation pipeline. The previous async_validation/_validate
        # branch referenced attributes that BaseValidatedType does not define.
        validated_content = await self.field.clean(
            content,
            model_instance=self.instance,
        )
        generated_name = self.field.generate_filename(self.instance, name)
        if len(generated_name) > self.field.max_length:
            raise ValueError(
                f"Generated storage name exceeds FileField max_length "
                f"({self.field.max_length})."
            )
        saved_name = await self._require_storage().save(
            generated_name,
            validated_content,
            max_length=self.field.max_length,
        )

        self._name = saved_name
        self._file = None
        self._committed = True
        self._set_instance_attribute(saved_name)
        return saved_name

    async def save(self, name: str, content: Any) -> str:
        """Django-inspired alias for :meth:`store` (does not commit the model)."""
        return await self.store(name, content)

    async def delete(self) -> None:
        """Delete the stored object and clear the mapped filename.

        Storage errors propagate. Clearing the database attribute after a failed
        deletion would hide an orphaned object and make retries impossible.
        This operation does not commit the caller's database transaction.
        """
        if not self:
            return

        name = self._required_name()
        self.close()
        try:
            await self._require_storage().delete(name)
        except Exception:
            logger.exception("Failed to delete stored file %s", name)
            raise

        self._name = None
        self._file = None
        self._committed = False
        self._set_instance_attribute(None)

    async def exists(self) -> bool:
        """Check if file exists in storage"""
        if not self.name:
            return False
        return await self._require_storage().exists(self.name)

    async def get_accessed_time(self):
        """Get last accessed time"""
        self._require_file()
        method = getattr(self._require_storage(), "get_accessed_time", None)
        if not callable(method):
            raise NotImplementedError(
                "The configured storage has no accessed-time metadata"
            )
        return await method(self.name)

    async def get_created_time(self):
        """Get creation time"""
        self._require_file()
        method = getattr(self._require_storage(), "get_created_time", None)
        if not callable(method):
            raise NotImplementedError(
                "The configured storage has no created-time metadata"
            )
        return await method(self.name)

    async def get_modified_time(self):
        """Get last modified time"""
        self._require_file()
        method = getattr(self._require_storage(), "get_modified_time", None)
        if not callable(method):
            raise NotImplementedError(
                "The configured storage has no modified-time metadata"
            )
        return await method(self.name)

    def __getstate__(self):
        """
        Pickle support: save only essential state.
        The FileDescriptor will restore the rest on unpickling.
        """
        return {
            "name": self.name,
            "closed": False,
            "_committed": True,
            "_file": None,
            "instance": self.instance,
            "field": self.field,
        }

    def __setstate__(self, state):
        """Restore state from pickle."""
        state = dict(state)
        self._name = state.pop("name")
        self.__dict__.update(state)
        self.storage = self.field.storage

    async def __aenter__(self):
        return await self.open()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.close()


class FileDescriptor:
    """
    Descriptor that provides FieldFile interface on model instances
    while preserving SQLAlchemy query capabilities.

    - SQLAlchemy sees only the raw string column.
    - Python code sees a FieldFile instance.
    """

    def __init__(self, field: "FileField", instrumented_attr):
        self.field = field
        self._instrumented_attr = instrumented_attr  # Original SQLAlchemy attribute
        self.cache_attr_name = f"_{self.field.attname}_file"

    def __get__(self, instance, owner=None):
        if instance is None:
            # Class-level access: give SQLAlchemy back its InstrumentedAttribute
            return self._instrumented_attr

        # Raw value from the mapped column (string path, None, maybe file-like)
        raw_value = self._instrumented_attr.__get__(instance, owner)

        cached = getattr(instance, self.cache_attr_name, None)

        # Fast path: we already have a FieldFile whose name matches the DB value
        if isinstance(cached, FieldFile) and (
            not cached._committed or cached.name == raw_value
        ):
            if cached.instance is not instance:
                cached.instance = instance
            return cached

        # Case 1: string path or None → new FieldFile
        if isinstance(raw_value, str) or raw_value is None:
            file_obj = self.field.attr_class(instance, self.field, raw_value)
            setattr(instance, self.cache_attr_name, file_obj)
            return file_obj

        # Case 2: raw_value is already a FieldFile (for example, assigned manually).
        if isinstance(raw_value, FieldFile):
            raw_value.instance = instance
            raw_value.field = self.field
            raw_value.storage = self.field.storage
            setattr(instance, self.cache_attr_name, raw_value)
            return raw_value

        # Case 3: raw_value is a file-like object (UploadFile, open file, etc.)
        if is_file_like(raw_value):
            # Wrap as "uncommitted" FieldFile.
            name = getattr(raw_value, "name", None)
            file_obj = self.field.attr_class(instance, self.field, name)
            file_obj._file = raw_value
            file_obj._committed = False

            # IMPORTANT: we do *not* write to the DB column here.
            # Persisting a name/path must happen via FieldFile.store(), which
            # calls setattr(instance, field.attname, self.name) after upload.
            setattr(instance, self.cache_attr_name, file_obj)
            return file_obj

        # Fallback: unexpected type → treat as a name-like string
        file_obj = self.field.attr_class(instance, self.field, str(raw_value))
        setattr(instance, self.cache_attr_name, file_obj)
        return file_obj

    def __set__(self, instance, value):
        """
        Setting the attribute updates both:
        - the underlying SQLAlchemy column (string path), and
        - the in-memory FieldFile cache (if applicable).
        """
        cache_attr_name = self.cache_attr_name

        # 1) FieldFile: store its name in DB, cache the object
        if isinstance(value, FieldFile):
            # Ensure FieldFile is bound correctly
            value.instance = instance
            value.field = self.field
            value.storage = self.field.storage
            value._committed = False  # Maybe a handmade FieldFile

            self._instrumented_attr.__set__(instance, value.name)
            setattr(instance, cache_attr_name, value)
            return

        # 2) str or None: write directly to DB, clear cache
        if isinstance(value, str) or value is None:
            self._instrumented_attr.__set__(instance, value)
            setattr(instance, cache_attr_name, None)
            return

        # 3) raw file-like: wrap into FieldFile; mark uncommitted;
        #    DO NOT auto-generate a name or touch storage here.
        #    The caller should call `await instance.<field>.store(...)`
        #    to upload and update DB.
        if is_file_like(value):
            name = getattr(value, "name", None)
            file_obj = self.field.attr_class(instance, self.field, name)
            file_obj._file = value
            file_obj._committed = False

            # Do not touch the DB column yet. The DB should only see a
            # stable path after storage has accepted the file.
            setattr(instance, cache_attr_name, file_obj)
            return

        # 4) Anything else is invalid
        raise TypeError(
            f"Invalid value for FileField {self.field.attname}: {type(value)!r}"
        )

    def __delete__(self, instance):
        # Delegate to SQLAlchemy's deleter and clear our cache
        self._instrumented_attr.__delete__(instance)
        if hasattr(instance, self.cache_attr_name):
            delattr(instance, self.cache_attr_name)


class FileField(BaseValidatedType):
    """
    SQLAlchemy FileField with Django-like behavior and async validation support.

    A storage backend is explicitly injected. Use
    ``AsyncStorageKitAdapter`` from ``async_orm_kit.integrations.storage`` to
    integrate the separately installed ``async-storage-kit`` package.

    Features:
    - Async storage backend integration
    - File upload with collision handling
    - Lazy file loading via FieldFile descriptor
    - Explicit storage cleanup through FieldFile.delete()
    - URL generation (public and presigned)
    - File metadata access (size, timestamps)
    - Path traversal protection
    - Custom upload path generation

    .. code-block:: python

        class User(Base):
            avatar = Column(FileField(
                upload_to="avatars/%Y/%m/",
                validators=[max_file_size(5*1024*1024)],
                max_length=255
            ))

        # Upload file (storage write; the caller owns the DB transaction)
        user = User()
        await user.avatar.save("profile.jpg", file_content)
        session.add(user)
        await session.commit()

        # Access file
        url = user.avatar.url
        size = await user.avatar.size()

        # Delete the object and clear the mapped name
        await user.avatar.delete()
        await session.commit()
    """

    impl = String
    cache_ok = True
    descriptor_class = FileDescriptor
    attr_class = FieldFile

    def __init__(
        self,
        *,
        upload_to: str | Callable = "",
        storage: StorageBackend | None = None,
        max_length: int = 255,
        validators: list[ValidatorType] | None = None,
        **kwargs,
    ):
        """
        Args:
            upload_to: Upload path or callable(instance, filename) -> path
            storage: Explicit async storage backend. No global default is created.
            max_length: Max filename/path length stored in DB
            validators: List of validators (sync or async)
        """
        self.upload_to = upload_to
        self.storage = storage
        self.max_length = max_length

        super().__init__(validators=validators, length=max_length, **kwargs)

    def _require_storage(self) -> StorageBackend:
        if self.storage is None:
            raise RuntimeError(
                "FileField requires an explicitly configured storage backend. "
                "Pass `storage=` when declaring the field."
            )
        return self.storage

    def _validate_upload_path(self, path: str) -> str:
        """Validate and normalize a relative POSIX storage prefix on every OS."""
        if not isinstance(path, str):
            raise TypeError("upload_to must resolve to a string")
        normalized = path.replace("\\", "/")
        if not normalized:
            return ""
        if (
            normalized.startswith("/")
            or re.match(r"^[a-zA-Z]:", normalized)
            or any(ord(char) < 32 or ord(char) == 127 for char in normalized)
        ):
            raise ValueError("Absolute paths and control characters are not allowed")

        # A trailing slash is conventional for upload directories; internal
        # empty components and dot/traversal components are never accepted.
        normalized = normalized.rstrip("/")
        components = normalized.split("/")
        if not normalized or any(part in {"", ".", ".."} for part in components):
            raise ValueError("Unsafe upload_to path component")
        return "/".join(components)

    def generate_filename(self, instance, filename: str) -> str:
        """Generate a platform-independent relative storage key."""
        if not isinstance(filename, str) or not filename.strip():
            raise ValueError("A non-empty filename is required")

        if callable(self.upload_to):
            upload_path = self.upload_to(instance, filename)
        else:
            from datetime import datetime

            upload_path = datetime.now().strftime(str(self.upload_to))

        upload_path = self._validate_upload_path(upload_path)

        # Client upload names may contain a browser-supplied path. Keep only
        # the basename; storage.generate_filename() performs backend filename
        # normalization (when available), and save() validates the final key.
        portable_filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        if portable_filename in {"", ".", ".."}:
            raise ValueError("Filename does not contain a usable basename")
        full_path = (
            f"{upload_path}/{portable_filename}" if upload_path else portable_filename
        )
        safe_input = self._validate_upload_path(full_path)
        generated_name = self._require_storage().generate_filename(safe_input)
        return self._validate_upload_path(generated_name)

    def contribute_to_class(self, cls, name, instrumented_attr):
        """Install FileDescriptor that wraps InstrumentedAttribute."""
        super().contribute_to_class(cls, name, instrumented_attr)
        cls._file_fields[name] = self
        descriptor = self.descriptor_class(self, instrumented_attr)
        setattr(cls, name, descriptor)

    def process_bind_param(self, value, dialect):
        """
        SQLAlchemy bind parameter processing.
        Handles file upload and sync validation.
        For async validators, call clean() explicitly before saving.
        """
        if value is None:
            return None

        # If it's a FieldFile, extract the name (its already saved)
        if isinstance(value, FieldFile):
            return value.name

        # File object - upload and validate
        if is_file_like(value):
            raise ValueError(
                "Cannot assign file objects directly. "
                "Use: await instance.<file_field>.store('name.jpg', file_obj)"
            )

        # For simple string paths (e.g., manual assignment), apply intrinsic
        # validation (e.g., max_length) and return resulting DB value.
        return super().process_bind_param(value, dialect)

    def process_result_value(self, value, dialect):
        """
        Process value loaded from database.
        Returns the raw path string (FieldFile wrapper is handled by descriptor).
        """
        return value

    async def clean(self, value, model_instance=None):
        """
        Full validation for file values.

        This method does not upload files. Uploads use ``FieldFile.save()``.
        It accepts ``None``, a previously saved ``FieldFile``, or a string key.
        File objects are rejected by ``process_bind_param``.
        """
        if value is None:
            return None

        # # If it's already our managed wrapper, we don't re-validate the path here.
        # if isinstance(value, FieldFile):
        #     return value

        try:
            # Use BaseValidatedType pipeline:
            #   validate() -> run_validators() -> arun_validators()
            cleaned = await super().clean(value, model_instance=model_instance)
            return cleaned
        except FieldValidationError:
            raise
        except Exception as e:
            field_name = self.attname or "file"
            raise FieldValidationError(
                f"Validation failed for file field '{field_name}': {e}"
            ) from e
