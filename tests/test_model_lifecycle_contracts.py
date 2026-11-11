"""Model serialization and real AsyncSession lifecycle/error contracts."""

from __future__ import annotations

from enum import Enum
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import Enum as SAEnum, ForeignKey
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column, relationship
from test_relationship_planning import Phase4Post, Phase4User

from async_orm_kit import Model, StringField
from async_orm_kit.exceptions import DatabaseError
from async_orm_kit.models.meta import Base


class RecordRole(Enum):
    ACTIVE = "active"


class LifecycleRecord(Model):
    __tablename__ = "lifecycle_contract_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(StringField(min_length=1, max_length=30))
    score: Mapped[int] = mapped_column(default=1)
    role: Mapped[RecordRole] = mapped_column(
        SAEnum(RecordRole), default=RecordRole.ACTIVE
    )


class SerializationOwner(Model):
    __tablename__ = "serialization_contract_owners"
    id: Mapped[int] = mapped_column(primary_key=True)
    detail_id: Mapped[int | None] = mapped_column(
        ForeignKey("serialization_contract_details.id")
    )
    detail: Mapped[SerializationDetail | None] = relationship()
    children: Mapped[list[SerializationChild]] = relationship()


class SerializationDetail(Base):
    __tablename__ = "serialization_contract_details"
    id: Mapped[int] = mapped_column(primary_key=True)


class SerializationChild(Base):
    __tablename__ = "serialization_contract_children"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("serialization_contract_owners.id")
    )


@pytest_asyncio.fixture
async def lifecycle_sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(LifecycleRecord.__table__.create)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def test_model_names_managers_metadata_and_serialization(monkeypatch):
    for name, expected in [
        ("ReleaseCity", "release_cities"),
        ("ReleaseBox", "release_boxes"),
        ("ReleaseToy", "release_toys"),
        ("Y", "ys"),
    ]:
        cls = type(
            name,
            (Model,),
            {
                "__annotations__": {"id": Mapped[int]},
                "id": mapped_column(primary_key=True),
            },
        )
        assert cls.__tablename__ == expected
    row = LifecycleRecord(id=1, name="one", role=RecordRole.ACTIVE)
    assert row.to_dict(enum_to_value=True)["role"] == "active"
    assert row.to_dict(include={"name"}) == {"name": "one"}
    assert "name" not in row.to_dict(exclude={"name"})
    assert str(row) == repr(row)
    assert LifecycleRecord.get_manager() is LifecycleRecord.objects
    assert LifecycleRecord.get_all_managers() == {"objects": LifecycleRecord.objects}
    with pytest.raises(AttributeError):
        LifecycleRecord.get_manager("absent")
    monkeypatch.setattr(LifecycleRecord, "_default_manager", None)
    assert LifecycleRecord.get_default_manager() is LifecycleRecord.objects
    assert LifecycleRecord.get_field_names() == ["id", "name", "score", "role"]
    assert "posts" in Phase4User.get_field_names(include_relationships=True)
    assert LifecycleRecord.get_primary_keys() == [
        "id"
    ] and LifecycleRecord.get_primary_key_names() == ["id"]
    owner = SerializationOwner(id=1)
    assert owner.to_dict(follow_relations=True)["detail"] is None
    owner.detail = SerializationDetail(id=2)
    owner.children = [SerializationChild(id=3)]
    result = owner.to_dict(follow_relations=True)
    assert result["detail"] == str(owner.detail) and result["children"] == [
        str(owner.children[0])
    ]
    assert owner.to_dict(include={"detail"}, follow_relations=True) == {
        "detail": str(owner.detail)
    }
    assert "children" not in owner.to_dict(exclude={"children"}, follow_relations=True)
    author = Phase4User(name="Author", posts=[Phase4Post(title="post")])
    assert author.to_dict(follow_relations=True)["posts"][0]["title"] == "post"
    assert author.posts[0].to_dict(follow_relations=True)["author"]["name"] == "Author"


@pytest.mark.asyncio
async def test_invalid_lifecycle_states_and_partial_updates(lifecycle_sessions):
    row = LifecycleRecord(name="new")
    assert not row._is_persisted()
    with pytest.raises(ValueError):
        await row.refresh_from_db(None)
    with pytest.raises(ValueError, match="inserting"):
        await row.save(None, update_fields=["name"])
    async with lifecycle_sessions() as session:
        session.add(row)
        with pytest.raises(ValueError):
            await row.refresh_from_db(session)
        await row.save(session)
        assert row._is_persisted()
        assert await row.save(session, update_fields=[]) is row
        with pytest.raises(ValueError):
            await row.save(session, force_insert=True)
        async with lifecycle_sessions() as other:
            with pytest.raises(ValueError, match="different"):
                await row.refresh_from_db(other)
        await row.full_clean(update_fields=["name"])
        await session.delete(row)
        await session.flush()
        with pytest.raises(ValueError):
            await row.save(session)
        with pytest.raises(ValueError):
            await row.refresh_from_db(session)


@pytest.mark.asyncio
async def test_model_database_failures_preserve_sqlalchemy_causes(
    lifecycle_sessions, monkeypatch
):
    async with lifecycle_sessions() as session:
        row = LifecycleRecord(name="one")
        await row.save(session)
        row.name = "dirty"
        row.score = 2
        with monkeypatch.context() as patch:
            failure = SQLAlchemyError("refresh failure")
            patch.setattr(session, "refresh", AsyncMock(side_effect=failure))
            with pytest.raises(DatabaseError, match="prepare") as error:
                await row.save(session, update_fields=["score"])
            assert error.value.cause is failure
        await session.rollback()
    async with lifecycle_sessions() as session:
        row = LifecycleRecord(name="two")
        with monkeypatch.context() as patch:
            patch.setattr(
                session,
                "flush",
                AsyncMock(side_effect=SQLAlchemyError("flush failure")),
            )
            with pytest.raises(DatabaseError, match="save"):
                await row.save(session)
        await session.rollback()
    async with lifecycle_sessions() as session:
        row = LifecycleRecord(name="three")
        await row.save(session)
        with monkeypatch.context() as patch:
            patch.setattr(
                session,
                "delete",
                AsyncMock(side_effect=SQLAlchemyError("delete failure")),
            )
            with pytest.raises(DatabaseError, match="delete"):
                await row.delete(session)


@pytest.mark.asyncio
async def test_partial_update_rejects_pending_relationship_changes():
    author = Phase4User(name="one", posts=[Phase4Post(title="pending")])
    with pytest.raises(ValueError, match="relationship changes"):
        await author._prepare_update_fields(None, {"name"})
