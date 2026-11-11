"""Adapter for the separately distributed ``async-storage-kit`` package.

The adapter intentionally uses structural typing and does not import
``async_storage_kit`` at runtime. Applications install the optional ``storage``
extra and pass a configured backend (or registry proxy) to the adapter.
"""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol, runtime_checkable

ConflictPolicy = Literal["rename", "overwrite", "error"]


@runtime_checkable
class AsyncStorage(Protocol):
    """The stable common subset of async-storage-kit's public contract."""

    async def save(
        self,
        name: str,
        content: Any,
        max_length: int | None = None,
        *,
        on_conflict: ConflictPolicy = "rename",
    ) -> str: ...

    def iter_chunks(
        self, name: str, chunk_size: int = 256 * 1024
    ) -> AsyncIterator[bytes]: ...

    async def read(self, name: str) -> bytes: ...

    async def delete(self, name: str, raise_on_missing: bool = False) -> bool: ...

    async def exists(self, name: str) -> bool: ...

    async def size(self, name: str) -> int: ...

    async def get_modified_time(self, name: str) -> Any: ...

    async def metadata(self, name: str) -> Any: ...


class LazyStorageProxyLike(Protocol):
    """Structural type for async-storage-kit's lazily resolving proxy."""

    @property
    def backend(self) -> AsyncStorage: ...


class AsyncStorageKitAdapter:
    """Adapt an ``async-storage-kit`` backend to ORM ``FileField``.

    ``async-storage-kit`` exposes byte reads and async chunk iteration instead
    of an ``open()`` method. The legacy ``FieldFile.open()`` API is therefore
    implemented with an in-memory ``BytesIO``; use ``FieldFile.iter_chunks()``
    for bounded-memory downloads.

    Args:
        storage: Configured ``async-storage-kit`` backend or registry proxy.
        on_conflict: Default save collision policy (``rename``, ``overwrite``
            or ``error``). The storage backend performs collision handling.

    The adapter does not own or close the backend. Configure backend lifetime
    at application scope and inject this adapter into ``FileField(storage=...)``.
    """

    _CONFLICT_POLICIES = frozenset({"rename", "overwrite", "error"})

    def __init__(
        self,
        storage: AsyncStorage | LazyStorageProxyLike,
        *,
        on_conflict: ConflictPolicy = "rename",
    ) -> None:
        if on_conflict not in self._CONFLICT_POLICIES:
            raise ValueError("on_conflict must be 'rename', 'overwrite', or 'error'.")

        if storage is None:
            raise TypeError("storage must be a configured async storage backend")

        # Do not inspect methods here: async-storage-kit's LazyStorageProxy
        # resolves its backend from __getattr__, so eager validation would
        # defeat lazy construction. Required methods are checked when invoked.
        self._storage = storage
        self._on_conflict = on_conflict

    @property
    def on_conflict(self) -> ConflictPolicy:
        return self._on_conflict

    def _required_method(self, name: str) -> Any:
        method = getattr(self._storage, name, None)
        if not callable(method):
            raise TypeError(
                f"Storage backend does not implement required method {name}()"
            )
        return method

    @property
    def backend(self) -> AsyncStorage | LazyStorageProxyLike:
        """Return the injected backend or proxy without resolving or owning it."""
        return self._storage

    async def save(
        self,
        name: str,
        content: Any,
        *,
        max_length: int | None = None,
    ) -> str:
        saved_name = await self._required_method("save")(
            name,
            content,
            max_length=max_length,
            on_conflict=self.on_conflict,
        )
        if not isinstance(saved_name, str) or not saved_name:
            raise TypeError("Storage backend save() must return a non-empty name.")
        return saved_name

    async def open(self, name: str, mode: str = "rb") -> io.BytesIO:
        """Return a buffered binary file handle for the legacy FileField API.

        This operation materializes the entire object in memory. Prefer
        ``iter_chunks()`` for large objects.
        """
        if mode != "rb":
            raise ValueError(
                "AsyncStorageKitAdapter.open() supports binary read mode 'rb' only."
            )
        return io.BytesIO(await self._required_method("read")(name))

    async def read(self, name: str) -> bytes:
        return await self._required_method("read")(name)

    def iter_chunks(
        self, name: str, chunk_size: int = 256 * 1024
    ) -> AsyncIterator[bytes]:
        if isinstance(chunk_size, bool) or not isinstance(chunk_size, int):
            raise TypeError("chunk_size must be an integer")
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        return self._required_method("iter_chunks")(name, chunk_size=chunk_size)

    async def delete(self, name: str) -> None:
        await self._required_method("delete")(name)

    async def exists(self, name: str) -> bool:
        return await self._required_method("exists")(name)

    async def size(self, name: str) -> int:
        return await self._required_method("size")(name)

    def generate_filename(self, name: str) -> str:
        """Use the backend's public filename helper when it provides one.

        ``generate_filename`` is a convenience on async-storage-kit's concrete
        ``StorageBackend`` but is intentionally not required by its structural
        ``AsyncStorage`` protocol. Custom protocol implementations may validate
        names in ``save()`` and therefore need no extra helper.
        """
        generator = getattr(self._storage, "generate_filename", None)
        return generator(name) if callable(generator) else name

    def path(self, name: str) -> str:
        method = getattr(self._storage, "path", None)
        if not callable(method):
            raise NotImplementedError(
                "The configured storage has no local path capability"
            )
        return method(name)

    def url(self, name: str) -> str:
        method = getattr(self._storage, "url", None)
        if not callable(method):
            raise NotImplementedError(
                "The configured storage has no public URL capability"
            )
        return method(name)

    async def get_presigned_url(self, name: str, expire: int = 3600) -> str:
        method = getattr(self._storage, "get_presigned_url", None)
        if not callable(method):
            raise NotImplementedError(
                "The configured storage has no signed URL capability"
            )
        return await method(name, expire=expire)

    async def get_modified_time(self, name: str) -> Any:
        return await self._required_method("get_modified_time")(name)

    async def get_accessed_time(self, name: str) -> Any:
        method = getattr(self._storage, "get_accessed_time", None)
        if not callable(method):
            raise NotImplementedError(
                "The configured storage does not track access time"
            )
        return await method(name)

    async def get_created_time(self, name: str) -> Any:
        method = getattr(self._storage, "get_created_time", None)
        if not callable(method):
            raise NotImplementedError(
                "The configured storage does not track creation time"
            )
        return await method(name)

    async def metadata(self, name: str) -> Any:
        return await self._required_method("metadata")(name)
