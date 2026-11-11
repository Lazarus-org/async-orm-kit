"""Mapped Python attribute names may differ from database column names."""

from __future__ import annotations

import pytest
from sqlalchemy import String
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

from async_orm_kit import Model
from async_orm_kit.query.relations.graph import JoinGraph


class RenamedRecord(Model):
    __tablename__ = "release_renamed_records"
    identity: Mapped[int] = mapped_column("record_id", primary_key=True)
    label: Mapped[str] = mapped_column("record_label", String(50))


@pytest.mark.asyncio
async def test_mapped_primary_key_create_filter_and_delete():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(RenamedRecord.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory.begin() as session:
            row = await RenamedRecord.objects.create(
                session, identity=7, label="mapped"
            )
            assert (await RenamedRecord.objects.get(session, pk=7)) is row
            assert await RenamedRecord.objects.filter(session, pk=7).delete() == 1
    finally:
        await engine.dispose()


def test_cleared_join_graph_retains_root_alias():
    graph = JoinGraph(RenamedRecord)
    graph.clear()
    assert graph.root.alias is RenamedRecord
    assert graph.is_empty


def test_renamed_columns_serialize_and_restore_mapped_values():
    row = RenamedRecord(identity=7, label="mapped")
    assert row.to_dict() == {"record_id": 7, "record_label": "mapped"}
    assert row.get_primary_key_values() == {"record_id": 7}
    assert "record_id=7" in repr(row)
    restored = RenamedRecord.from_dict({"record_id": 8, "record_label": "restored"})
    assert restored.identity == 8 and restored.label == "restored"
    restored.update_from_dict({"record_label": "updated"})
    assert restored.label == "updated"
