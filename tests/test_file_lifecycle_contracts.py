"""File wrapper behavior, legacy backend capabilities, and descriptor lifecycles."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from test_storage_adapter import FakeAsyncStorage, make_document

from async_orm_kit import AsyncStorageKitAdapter, FileField
from async_orm_kit.models.fields.exceptions import FieldValidationError
from async_orm_kit.models.fields.typed.file import FieldFile


class LegacyStorage:
    def __init__(self):
        self.data = b"content"
        self.opens = 0

    def generate_filename(self, name):
        return name

    async def save(self, name, content, **kwargs):
        return name

    async def open(self, name, mode="rb"):
        self.opens += 1
        return io.BytesIO(self.data)

    async def size(self, name):
        return len(self.data)

    async def exists(self, name):
        return True

    async def delete(self, name):
        return None


def wrapper(storage=None, name="asset.bin"):
    field = FileField(storage=storage)
    field.attname = "attachment"
    return FieldFile(None, field, name)


@pytest.mark.asyncio
async def test_legacy_file_read_reopen_size_and_context_management():
    storage = LegacyStorage()
    file = wrapper(storage)
    assert str(file) == "asset.bin" and file == "asset.bin"
    assert file == SimpleNamespace(name="asset.bin") and hash(file) == hash("asset.bin")
    assert file.closed
    with pytest.raises(RuntimeError, match="asynchronous"):
        _ = file.file
    opened = await file.open()
    opened.seek(2)
    assert (
        file.file is opened and await file.read() == b"content" and opened.tell() == 2
    )
    assert await file.size() == 7 and await file.exists()
    await file.open()
    assert opened.closed and storage.opens == 2
    async with file as opened_again:
        assert opened_again.read() == b"content"
    assert file.closed
    assert await file.read() == b"content"
    file.close()
    assert await wrapper(storage).read() == b"content"
    file.name = "renamed.bin"
    assert file.name == "renamed.bin"
    file.file = io.BytesIO(b"x")
    assert file.file.read() == b"x"
    file.close()
    del file.file
    file.close()


@pytest.mark.asyncio
async def test_reopenable_legacy_file_and_uncommitted_sizes():
    class Reopenable(io.BytesIO):
        size = 9

        def open(self, mode):
            self.seek(0)
            self.mode = mode

    file = wrapper(LegacyStorage())
    file.file = Reopenable(b"contents")
    assert await file.open("rb") is file.file and file.file.mode == "rb"
    file._committed = False
    assert await file.size() == 9
    file.file = io.BytesIO(b"1234")
    file.file.seek(1)
    assert await file.size() == 4 and file.file.tell() == 1
    file.file = None
    assert await file.size() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capability",
    [
        "path",
        "url",
        "get_presigned_url",
        "metadata",
        "get_accessed_time",
        "get_created_time",
        "get_modified_time",
        "iter_chunks",
    ],
)
async def test_optional_file_capabilities_fail_explicitly(capability):
    file = wrapper(LegacyStorage())
    with pytest.raises(NotImplementedError):
        if capability in {"path", "url"}:
            getattr(file, capability)
        elif capability == "iter_chunks":
            await anext(file.iter_chunks())
        else:
            await getattr(file, capability)()


@pytest.mark.asyncio
async def test_missing_file_and_storage_behaviors():
    empty = wrapper(LegacyStorage(), None)
    assert not empty and str(empty) == "" and not await empty.exists()
    assert await empty.delete() is None
    with pytest.raises(ValueError):
        await empty.open()
    file = wrapper()
    with pytest.raises(RuntimeError, match="configured storage"):
        await file.open()
    file._set_instance_attribute("x")
    owner = SimpleNamespace()
    unbound = FieldFile(owner, FileField(storage=LegacyStorage()), "x")
    unbound._set_instance_attribute("x")
    assert owner._None_file is unbound


@pytest.mark.asyncio
async def test_file_state_roundtrip_preserves_name_and_backend():
    file = wrapper(LegacyStorage())
    state = file.__getstate__()
    restored = FieldFile.__new__(FieldFile)
    restored.__setstate__(state)
    assert (
        restored.name == "asset.bin"
        and restored.storage is file.storage
        and restored.closed
    )
    assert await restored.size() == 7


def test_descriptor_assignments_preserve_uncommitted_upload_until_store():
    model, adapter = make_document(FakeAsyncStorage())
    instance = model(attachment="original.bin")
    descriptor = model.__dict__["attachment"]
    assert descriptor.__get__(None, model) is model.attachment
    uploaded = io.BytesIO(b"upload")
    uploaded.name = "upload.bin"
    instance.attachment = uploaded
    pending = instance.attachment
    assert pending.file is uploaded and not pending._committed
    assert descriptor._instrumented_attr.__get__(instance, model) == "original.bin"
    pending.instance = None
    assert instance.attachment.instance is instance
    manually = FieldFile(None, descriptor.field, "manual.bin")
    instance.attachment = manually
    assert instance.attachment is manually and manually.storage is adapter
    with pytest.raises(TypeError):
        instance.attachment = object()
    del instance.attachment
    instance.attachment = "next.bin"
    delattr(instance, descriptor.cache_attr_name)
    del instance.attachment


@pytest.mark.parametrize("raw", [24, io.BytesIO(b"raw")])
def test_descriptor_wraps_legacy_raw_values(raw):
    model, _ = make_document(FakeAsyncStorage())
    instance = model()
    descriptor = model.__dict__["attachment"]
    if isinstance(raw, io.BytesIO):
        raw.name = "raw.bin"
    descriptor._instrumented_attr.__set__(instance, raw)
    file = instance.attachment
    assert file.name == ("raw.bin" if isinstance(raw, io.BytesIO) else "24")
    if isinstance(raw, io.BytesIO):
        assert file.file is raw and not file._committed


def test_descriptor_binds_a_raw_fieldfile():
    model, adapter = make_document(FakeAsyncStorage())
    instance = model()
    descriptor = model.__dict__["attachment"]
    file = wrapper(LegacyStorage())
    descriptor._instrumented_attr.__set__(instance, file)
    assert instance.attachment is file
    assert file.instance is instance and file.storage is adapter


@pytest.mark.asyncio
async def test_field_validation_and_bind_failures():
    field = FileField(
        storage=LegacyStorage(), upload_to=lambda instance, name: "uploads"
    )
    assert field.generate_filename(None, "input.bin") == "uploads/input.bin"
    assert field.process_bind_param(wrapper(LegacyStorage()), None) == "asset.bin"
    with pytest.raises(ValueError, match="file objects"):
        field.process_bind_param(io.BytesIO(), None)
    assert await field.clean(None) is None
    for path in [
        42,
        "/absolute",
        "C:/root",
        "bad\x00name",
        "bad\x7fname",
        "a//b",
        "..",
        "/",
    ]:
        with pytest.raises((ValueError, TypeError)):
            field._validate_upload_path(path)
    for name in ["", None, "/", ".."]:
        with pytest.raises(ValueError):
            field.generate_filename(None, name)
    file = FieldFile(None, field, "asset.bin")
    with pytest.raises(ValueError):
        await file.store("", b"content")

    async def invalid(value):
        raise TypeError("invalid async type")

    for attname in [None, "attachment"]:
        invalid_field = FileField(storage=LegacyStorage(), validators=[invalid])
        invalid_field.attname = attname
        with pytest.raises(FieldValidationError, match="Validation failed"):
            await invalid_field.clean("asset.bin")
    with pytest.raises(FieldValidationError):
        await FileField(
            validators=[
                lambda value: (_ for _ in ()).throw(FieldValidationError("bad"))
            ]
        ).clean("asset.bin")


@pytest.mark.asyncio
async def test_adapter_missing_capabilities_and_invalid_backend_results():
    with pytest.raises(TypeError):
        AsyncStorageKitAdapter(None)
    adapter = AsyncStorageKitAdapter(LegacyStorage())
    with pytest.raises(ValueError):
        adapter.iter_chunks("asset", 0)
    for name in [
        "path",
        "url",
        "get_presigned_url",
        "get_accessed_time",
        "get_created_time",
    ]:
        with pytest.raises(NotImplementedError):
            result = getattr(adapter, name)("asset")
            if name not in {"path", "url"}:
                await result

    class BadSave(LegacyStorage):
        async def save(self, *args, **kwargs):
            return None

    with pytest.raises(TypeError, match="non-empty name"):
        await AsyncStorageKitAdapter(BadSave()).save("asset", b"data")
