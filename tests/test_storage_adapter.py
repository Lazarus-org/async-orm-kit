from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Mapped, Session, mapped_column

from async_orm_kit import AsyncStorageKitAdapter, FileField, Model
from async_orm_kit.models.fields import FieldValidationError


class FakeAsyncStorage:
    """Small structural test double for async-storage-kit public methods."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.last_conflict_policy: str | None = None
        self.fail_delete = False
        self.fail_save = False
        self.stream_closed = False
        self.modified_at = datetime(2026, 11, 9, tzinfo=UTC)

    async def save(
        self,
        name: str,
        content: Any,
        max_length: int | None = None,
        *,
        on_conflict: str = "rename",
    ) -> str:
        self.last_conflict_policy = on_conflict
        if self.fail_save:
            raise RuntimeError("save failed")
        if isinstance(content, bytes):
            data = content
        elif isinstance(content, bytearray):
            data = bytes(content)
        elif hasattr(content, "read"):
            data = content.read()
        else:
            data = b"".join([chunk async for chunk in content])

        if max_length is not None and len(name) > max_length:
            raise ValueError("name exceeds max_length")

        candidate = name
        if candidate in self.objects:
            if on_conflict == "error":
                raise FileExistsError(candidate)
            if on_conflict == "rename":
                stem, dot, suffix = name.rpartition(".")
                if not dot:
                    stem, suffix = name, ""
                index = 1
                while candidate in self.objects:
                    candidate = f"{stem}_{index}{'.' + suffix if suffix else ''}"
                    index += 1
        self.objects[candidate] = data
        return candidate

    def generate_filename(self, name: str) -> str:
        return name.replace(" ", "_")

    async def read(self, name: str) -> bytes:
        return self.objects[name]

    async def delete(self, name: str, raise_on_missing: bool = False) -> bool:
        if self.fail_delete:
            raise RuntimeError("delete failed")
        existed = name in self.objects
        self.objects.pop(name, None)
        if raise_on_missing and not existed:
            raise FileNotFoundError(name)
        return existed

    async def exists(self, name: str) -> bool:
        return name in self.objects

    async def size(self, name: str) -> int:
        return len(self.objects[name])

    async def get_modified_time(self, name: str) -> datetime:
        self.objects[name]
        return self.modified_at

    async def metadata(self, name: str) -> Any:
        return SimpleNamespace(
            name=name,
            size=await self.size(name),
            modified_at=await self.get_modified_time(name),
        )

    async def iter_chunks(self, name: str, chunk_size: int = 256 * 1024):
        data = self.objects[name]
        try:
            for offset in range(0, len(data), chunk_size):
                yield data[offset : offset + chunk_size]
        finally:
            self.stream_closed = True

    def url(self, name: str) -> str:
        return f"https://files.example.test/{name}"

    def path(self, name: str) -> str:
        return f"/tmp/storage/{name}"

    async def get_presigned_url(self, name: str, expire: int = 3600) -> str:
        return f"https://signed.example.test/{name}?expires={expire}"

    async def get_accessed_time(self, name: str) -> datetime:
        self.objects[name]
        return self.modified_at

    async def get_created_time(self, name: str) -> datetime:
        self.objects[name]
        return self.modified_at


def make_document(storage: FakeAsyncStorage, *, upload_to: str = "documents"):
    adapter = AsyncStorageKitAdapter(storage)
    class_name = f"StorageAdapterDocument_{id(storage)}"
    model = type(
        class_name,
        (Model,),
        {
            "__module__": __name__,
            "__tablename__": f"storage_adapter_document_{id(storage)}",
            "__annotations__": {
                "id": Mapped[int],
                "attachment": Mapped[str | None],
            },
            "id": mapped_column(primary_key=True),
            "attachment": mapped_column(
                FileField(storage=adapter, upload_to=upload_to), nullable=True
            ),
        },
    )
    return model, adapter


def run(coro):
    return asyncio.run(coro)


def test_adapter_validates_conflict_policy_and_backend_contract() -> None:
    with pytest.raises(ValueError, match="on_conflict"):
        AsyncStorageKitAdapter(
            FakeAsyncStorage(),
            on_conflict="invalid",  # type: ignore[arg-type]
        )

    adapter = AsyncStorageKitAdapter(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="required method read"):
        run(adapter.read("file.txt"))


def test_adapter_passes_configured_conflict_policy_and_exposes_backend() -> None:
    storage = FakeAsyncStorage()
    adapter = AsyncStorageKitAdapter(storage, on_conflict="overwrite")

    async def scenario() -> None:
        await adapter.save("report.txt", b"first")
        await adapter.save("report.txt", b"second")

    run(scenario())
    assert adapter.backend is storage
    assert storage.last_conflict_policy == "overwrite"
    assert storage.objects["report.txt"] == b"second"


def test_adapter_open_is_buffered_binary_read_and_rejects_text_modes() -> None:
    storage = FakeAsyncStorage()
    adapter = AsyncStorageKitAdapter(storage)

    async def scenario() -> None:
        await storage.save("sample.bin", b"payload")
        file_obj = await adapter.open("sample.bin")
        assert file_obj.read() == b"payload"
        with pytest.raises(ValueError, match="binary read mode"):
            await adapter.open("sample.bin", "r")

    run(scenario())


def test_adapter_streaming_validates_chunk_size() -> None:
    storage = FakeAsyncStorage()
    adapter = AsyncStorageKitAdapter(storage)

    with pytest.raises(TypeError, match="integer"):
        adapter.iter_chunks("file.bin", True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="positive"):
        adapter.iter_chunks("file.bin", 0)


def test_filefield_store_save_read_stream_metadata_and_delete() -> None:
    storage = FakeAsyncStorage()
    Document, _adapter = make_document(storage)
    document = Document()

    async def scenario() -> None:
        saved_name = await document.attachment.store("report file.txt", b"abcdef")
        assert saved_name == "documents/report_file.txt"
        assert document.attachment.name == saved_name
        assert await document.attachment.exists()
        assert await document.attachment.size() == 6
        assert await document.attachment.read() == b"abcdef"
        assert document.attachment.url.endswith(saved_name)
        assert document.attachment.path.endswith(saved_name)
        assert await document.attachment.get_presigned_url(90) == (
            f"https://signed.example.test/{saved_name}?expires=90"
        )
        assert await document.attachment.get_modified_time() == storage.modified_at
        assert await document.attachment.get_accessed_time() == storage.modified_at
        assert await document.attachment.get_created_time() == storage.modified_at

        metadata = await document.attachment.metadata()
        assert metadata.name == saved_name
        assert metadata.size == 6

        chunks = [chunk async for chunk in document.attachment.iter_chunks(2)]
        assert chunks == [b"ab", b"cd", b"ef"]

        file_obj = await document.attachment.open()
        assert file_obj.read() == b"abcdef"
        document.attachment.close()

        await document.attachment.delete()
        assert document.attachment.name is None
        assert not storage.objects

    run(scenario())


def test_filefield_save_alias_and_rename_collision_policy() -> None:
    storage = FakeAsyncStorage()
    Document, _adapter = make_document(storage)
    document = Document()

    async def scenario() -> None:
        first = await document.attachment.save("same.txt", b"first")
        second = await document.attachment.save("same.txt", b"second")
        assert first == "documents/same.txt"
        assert second != first
        assert await storage.read(first) == b"first"
        assert await storage.read(second) == b"second"

    run(scenario())


def test_filefield_delete_failure_preserves_name_for_retry() -> None:
    storage = FakeAsyncStorage()
    Document, _adapter = make_document(storage)
    document = Document()

    async def scenario() -> None:
        saved_name = await document.attachment.store("retry.txt", b"data")
        storage.fail_delete = True
        with pytest.raises(RuntimeError, match="delete failed"):
            await document.attachment.delete()
        assert document.attachment.name == saved_name
        assert await storage.exists(saved_name)

        storage.fail_delete = False
        await document.attachment.delete()
        assert document.attachment.name is None

    run(scenario())


def test_filefield_rejects_unsafe_upload_prefixes() -> None:
    storage = FakeAsyncStorage()
    Document, _adapter = make_document(storage, upload_to="../private")
    document = Document()

    with pytest.raises(ValueError, match="Unsafe upload_to"):
        run(document.attachment.store("secret.txt", b"data"))
    assert not storage.objects


def test_filefield_upload_prefix_is_platform_independent() -> None:
    storage = FakeAsyncStorage()
    Document, _adapter = make_document(storage, upload_to="uploads\\2026\\11\\")
    document = Document()

    saved_name = run(
        document.attachment.store(r"C:\\fakepath\\profile photo.png", b"image")
    )
    assert saved_name == "uploads/2026/11/profile_photo.png"


def test_filefield_validators_run_before_storage_write() -> None:
    storage = FakeAsyncStorage()

    def reject_content(content: Any) -> None:
        if content == b"blocked":
            raise ValueError("blocked content")

    adapter = AsyncStorageKitAdapter(storage)

    class ValidatedDocument(Model):
        __tablename__ = f"validated_storage_document_{id(storage)}"

        id: Mapped[int] = mapped_column(primary_key=True)
        attachment: Mapped[str | None] = mapped_column(
            FileField(storage=adapter, validators=[reject_content]), nullable=True
        )

    document = ValidatedDocument()
    with pytest.raises(FieldValidationError, match="blocked content"):
        run(document.attachment.store("blocked.txt", b"blocked"))
    assert not storage.objects


def test_core_import_does_not_import_optional_storage_package() -> None:
    source_root = Path(__file__).parents[1] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source_root)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import async_orm_kit; "
            "assert 'async_storage_kit' not in sys.modules",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_adapter_does_not_resolve_lazy_backend_during_construction() -> None:
    backend = FakeAsyncStorage()

    class LazyProxy:
        def __init__(self) -> None:
            self.resolved = False

        def __getattr__(self, name: str) -> Any:
            self.resolved = True
            return getattr(backend, name)

    proxy = LazyProxy()
    adapter = AsyncStorageKitAdapter(proxy)  # type: ignore[arg-type]
    assert proxy.resolved is False

    run(adapter.save("lazy.txt", b"lazy"))
    assert proxy.resolved is True
    assert backend.objects["lazy.txt"] == b"lazy"


def test_filefield_upload_accepts_async_byte_iterables() -> None:
    storage = FakeAsyncStorage()
    Document, _adapter = make_document(storage)
    document = Document()

    async def content():
        yield b"chunk-1"
        yield b"chunk-2"

    saved_name = run(document.attachment.store("stream.bin", content()))
    assert run(storage.read(saved_name)) == b"chunk-1chunk-2"


def test_filefield_enforces_generated_storage_key_length() -> None:
    storage = FakeAsyncStorage()
    adapter = AsyncStorageKitAdapter(storage)

    class ShortNameDocument(Model):
        __tablename__ = f"short_storage_name_{id(storage)}"

        id: Mapped[int] = mapped_column(primary_key=True)
        attachment: Mapped[str | None] = mapped_column(
            FileField(storage=adapter, upload_to="long-prefix", max_length=12),
            nullable=True,
        )

    document = ShortNameDocument()
    with pytest.raises(ValueError, match="max_length"):
        run(document.attachment.store("report.txt", b"data"))
    assert not storage.objects


def test_filefield_storage_failure_does_not_change_mapped_name() -> None:
    storage = FakeAsyncStorage()
    Document, _adapter = make_document(storage)
    document = Document()

    async def scenario() -> None:
        original = await document.attachment.store("original.txt", b"original")
        storage.fail_save = True
        with pytest.raises(RuntimeError, match="save failed"):
            await document.attachment.store("replacement.txt", b"replacement")
        assert document.attachment.name == original
        assert await storage.exists(original)

    run(scenario())


def test_fieldfile_stream_closes_backend_iterator_when_consumer_stops() -> None:
    storage = FakeAsyncStorage()
    Document, _adapter = make_document(storage)
    document = Document()

    async def scenario() -> None:
        await document.attachment.store("stream.txt", b"abcdef")
        iterator = document.attachment.iter_chunks(2)
        assert await anext(iterator) == b"ab"
        await iterator.aclose()
        assert storage.stream_closed is True

    run(scenario())


def test_filefield_runs_async_validators_before_upload() -> None:
    storage = FakeAsyncStorage()

    async def reject_invalid_content(content: Any) -> None:
        await asyncio.sleep(0)
        if content == b"invalid":
            raise ValueError("async content validation failed")

    adapter = AsyncStorageKitAdapter(storage)

    class AsyncValidatedDocument(Model):
        __tablename__ = f"async_validated_storage_document_{id(storage)}"

        id: Mapped[int] = mapped_column(primary_key=True)
        attachment: Mapped[str | None] = mapped_column(
            FileField(storage=adapter, validators=[reject_invalid_content]),
            nullable=True,
        )

    document = AsyncValidatedDocument()
    with pytest.raises(FieldValidationError, match="async content validation failed"):
        run(document.attachment.store("invalid.txt", b"invalid"))
    assert not storage.objects

    saved_name = run(document.attachment.store("valid.txt", b"valid"))
    assert run(storage.read(saved_name)) == b"valid"


def test_filefield_storage_key_round_trips_through_sqlalchemy() -> None:
    storage = FakeAsyncStorage()
    Document, _adapter = make_document(storage)
    engine = create_engine("sqlite+pysqlite:///:memory:")

    try:
        Document.__table__.create(engine)
        with Session(engine) as session:
            document = Document()
            saved_name = run(document.attachment.store("persisted.txt", b"database"))
            session.add(document)
            session.commit()
            document_id = document.id
            session.expunge_all()

            loaded = session.get(Document, document_id)
            assert loaded is not None
            assert loaded.attachment.name == saved_name
            assert run(loaded.attachment.read()) == b"database"
    finally:
        engine.dispose()


def test_filefield_rejects_none_content_before_storage_write() -> None:
    storage = FakeAsyncStorage()
    Document, _adapter = make_document(storage)
    document = Document()

    with pytest.raises(ValueError, match="content must not be None"):
        run(document.attachment.store("empty.txt", None))
    assert not storage.objects
