from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

import pytest

storage_kit = pytest.importorskip("async_storage_kit")
pytest.importorskip("aiosqlite")
from async_storage_kit import (  # noqa: E402
    FileSystemStorage,
    LazyStorageProxy,
    MemoryStorage,
    StorageRegistry,
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.orm import Mapped, mapped_column  # noqa: E402

from async_orm_kit import AsyncStorageKitAdapter, FileField, Model  # noqa: E402


def make_model(storage: Any):
    adapter = AsyncStorageKitAdapter(storage)
    suffix = uuid4().hex
    model = type(
        f"StorageKitDocument_{suffix}",
        (Model,),
        {
            "__module__": __name__,
            "__tablename__": f"storage_kit_document_{suffix}",
            "__annotations__": {
                "id": Mapped[int],
                "attachment": Mapped[str | None],
            },
            "id": mapped_column(primary_key=True),
            "attachment": mapped_column(
                FileField(storage=adapter, upload_to="documents/%Y/%m"),
                nullable=True,
            ),
        },
    )
    return model, adapter


@pytest.mark.asyncio
async def test_memory_storage_filefield_lifecycle() -> None:
    storage = MemoryStorage()
    Document, _adapter = make_model(storage)
    document = Document()

    saved_name = await document.attachment.save("report.txt", b"async storage kit")
    assert saved_name.startswith("documents/")
    assert await document.attachment.exists()
    assert await document.attachment.read() == b"async storage kit"
    assert await document.attachment.size() == len(b"async storage kit")

    metadata = await document.attachment.metadata()
    assert metadata.name == saved_name
    assert metadata.size == len(b"async storage kit")
    assert metadata.modified_at.tzinfo is not None

    chunks = [chunk async for chunk in document.attachment.iter_chunks(4)]
    assert b"".join(chunks) == b"async storage kit"

    # open() is the backwards-compatible, buffered API.
    file_obj = await document.attachment.open()
    assert file_obj.read() == b"async storage kit"
    document.attachment.close()

    await document.attachment.delete()
    assert document.attachment.name is None
    assert not await storage.exists(saved_name)


@pytest.mark.asyncio
async def test_filesystem_storage_path_and_public_url_capabilities() -> None:
    with TemporaryDirectory() as directory:
        storage = FileSystemStorage(directory, base_url="/media/")
        Document, _adapter = make_model(storage)
        document = Document()

        saved_name = await document.attachment.store("avatar.png", b"image-data")
        assert Path(document.attachment.path).is_file()
        assert document.attachment.url == f"/media/{saved_name}"
        assert await document.attachment.read() == b"image-data"

        await document.attachment.delete()
        assert not Path(directory, *saved_name.split("/")).exists()


@pytest.mark.asyncio
async def test_storage_registry_backend_proxy_is_accepted() -> None:
    created: list[MemoryStorage] = []

    def create_storage() -> MemoryStorage:
        backend = MemoryStorage()
        created.append(backend)
        return backend

    registry = StorageRegistry({"default": create_storage})
    proxy = LazyStorageProxy(registry, "default")
    Document, _adapter = make_model(proxy)
    assert created == []
    document = Document()

    saved_name = await document.attachment.store("registry.txt", b"registry")
    assert created
    assert await document.attachment.read() == b"registry"
    await document.attachment.delete()
    assert not await created[0].exists(saved_name)


@pytest.mark.asyncio
async def test_adapter_honors_storage_kit_overwrite_policy() -> None:
    storage = MemoryStorage()
    adapter = AsyncStorageKitAdapter(storage, on_conflict="overwrite")

    class OverwriteDocument(Model):
        __tablename__ = f"storage_kit_overwrite_document_{uuid4().hex}"

        id: Mapped[int] = mapped_column(primary_key=True)
        attachment: Mapped[str | None] = mapped_column(
            FileField(storage=adapter, upload_to="documents"), nullable=True
        )

    document = OverwriteDocument()

    first = await document.attachment.store("same.txt", b"first")
    second = await document.attachment.store("same.txt", b"second")

    assert first == second
    assert await storage.read(second) == b"second"


@pytest.mark.asyncio
async def test_filefield_storage_key_persists_with_real_async_session(
    tmp_path: Path,
) -> None:
    storage = MemoryStorage()
    Document, _adapter = make_model(storage)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'filefield.sqlite3'}"
    )

    table_created = False
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Document.__table__.create)
        table_created = True

        async with AsyncSession(engine, expire_on_commit=False) as session:
            document = Document()
            saved_name = await document.attachment.save(
                "persistent.txt",
                b"stored outside the database",
            )
            session.add(document)
            await session.commit()
            document_id = document.id

        async with AsyncSession(engine, expire_on_commit=False) as session:
            loaded = await session.get(Document, document_id)
            assert loaded is not None
            assert loaded.attachment.name == saved_name
            assert await loaded.attachment.read() == b"stored outside the database"

            await loaded.attachment.delete()
            await session.commit()
            assert loaded.attachment.name is None
            assert not await storage.exists(saved_name)
    finally:
        if table_created:
            async with engine.begin() as connection:
                await connection.run_sync(Document.__table__.drop)
        await engine.dispose()
