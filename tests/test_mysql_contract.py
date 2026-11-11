"""Offline MySQL dialect regression tests; does not import asyncmy."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import JSON, ForeignKey, Integer, String, Uuid
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from async_orm_kit import Model
from async_orm_kit.observers.queryset_observer import BaseQuerySetObserver
from async_orm_kit.queryset.queryset import QuerySet


class MySQLContractParent(Model):
    __tablename__ = "mysql_contract_parents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    children: Mapped[list[MySQLContractItem]] = relationship(back_populates="parent")


class MySQLContractItem(Model):
    __tablename__ = "mysql_contract_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)
    token: Mapped[UUID] = mapped_column(Uuid(), default=uuid4)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("mysql_contract_parents.id")
    )
    parent: Mapped[MySQLContractParent | None] = relationship(back_populates="children")


class FakeResult:
    def __init__(self, rowcount=1, scalar_values=()):
        self.rowcount = rowcount
        self.scalar_values = list(scalar_values)

    def scalars(self):
        return self

    def all(self):
        return self.scalar_values


class FakeConnection:
    def __init__(self, session):
        self.session = session

    async def execute(self, statement, values=None):
        self.session.commands.append((statement, values))
        return FakeResult(rowcount=self.session.rowcount)


class FakeMySQLSession:
    def __init__(self, *, rowcount=1, selected_keys=(1,)):
        self.dialect = mysql.dialect()
        self.bind = SimpleNamespace(dialect=self.dialect)
        self.rowcount = rowcount
        self.selected_keys = list(selected_keys)
        self.commands = []
        self.added = []
        self.refreshed = []
        self.flush_count = 0

    def get_bind(self, mapper=None):
        return self.bind

    def add_all(self, objects):
        self.added.extend(objects)

    async def flush(self):
        self.flush_count += 1
        for index, instance in enumerate(self.added, 1):
            if instance.id is None:
                instance.id = index
            if instance.token is None:
                instance.token = uuid4()

    async def refresh(self, instance):
        self.refreshed.append(instance)

    async def connection(self):
        return FakeConnection(self)

    async def execute(self, statement, values=None, **kwargs):
        self.commands.append((statement, values))
        if str(statement).lstrip().upper().startswith("SELECT"):
            return FakeResult(scalar_values=self.selected_keys)
        return FakeResult(rowcount=self.rowcount)


def qs(session):
    return QuerySet(MySQLContractItem, session)


def run(awaitable):
    return asyncio.run(awaitable)


def test_mysql_returning_bulk_insert_uses_flush_and_refresh_not_native_returning():
    session = FakeMySQLSession()
    records = run(
        qs(session).bulk_create(
            [{"slug": "a", "payload": {"x": 1}}, {"slug": "b", "payload": {"x": 2}}],
            returning=True,
        )
    )
    assert len(records) == 2
    assert session.flush_count == 1
    assert session.refreshed == records
    assert session.commands == []
    assert all(record.id is not None for record in records)
    assert len({record.token for record in records}) == 2


def test_mysql_upsert_compiles_with_on_duplicate_key_update_and_affected_rows():
    session = FakeMySQLSession(rowcount=2)
    count = run(
        qs(session).bulk_create(
            [{"slug": "old", "payload": {"version": 2}}],
            update_conflicts=True,
            unique_fields=["slug"],
            update_fields=["payload"],
        )
    )
    assert count == 2  # MySQL affected-row count, not a logical object count
    statement, params = session.commands[-1]
    compiled = str(statement.compile(dialect=mysql.dialect()))
    assert "ON DUPLICATE KEY UPDATE" in compiled
    assert "RETURNING" not in compiled
    assert params[0]["slug"] == "old"


def test_mysql_insert_ignore_uses_dialect_specific_prefix():
    session = FakeMySQLSession(rowcount=1)
    count = run(
        qs(session).bulk_create(
            [{"slug": "existing", "payload": {}}, {"slug": "new", "payload": {}}],
            ignore_conflicts=True,
        )
    )
    assert count == 1
    statement, _ = session.commands[-1]
    assert "INSERT IGNORE INTO" in str(statement.compile(dialect=mysql.dialect()))


def test_mysql_returning_conflict_and_update_fail_before_write():
    session = FakeMySQLSession()
    with pytest.raises(NotImplementedError, match="MySQL 8"):
        run(
            qs(session).bulk_create(
                [{"slug": "a", "payload": {}}],
                returning=True,
                update_conflicts=True,
                update_fields=["payload"],
                unique_fields=["slug"],
            )
        )
    with pytest.raises(NotImplementedError, match="MySQL 8"):
        run(qs(session).filter(slug="a").update(returning=True, payload={"x": 1}))
    assert session.flush_count == 0
    assert session.commands == []


class RecordingDeleteObserver(BaseQuerySetObserver):
    def __init__(self):
        self.ids = []

    async def post_bulk_delete(self, **kwargs):
        self.ids = list(kwargs["doc_ids"])


def test_mysql_observed_delete_locks_primary_keys_instead_of_returning():
    session = FakeMySQLSession(selected_keys=[3, 4], rowcount=2)
    observer = RecordingDeleteObserver()
    count = run(qs(session).filter(slug="delete").add_observer(observer).delete())
    assert count == 2
    assert observer.ids == [3, 4]
    assert len(session.commands) == 2
    select_stmt = session.commands[0][0]
    delete_stmt = session.commands[1][0]
    assert "FOR UPDATE" in str(select_stmt.compile(dialect=mysql.dialect()))
    assert "RETURNING" not in str(delete_stmt.compile(dialect=mysql.dialect()))


def test_mysql_update_without_returning_compiles_standard_update():
    session = FakeMySQLSession(rowcount=1)
    updated = run(qs(session).filter(slug="old").update(payload={"version": 3}))
    assert updated == 1
    statement = session.commands[-1][0]
    compiled = str(statement.compile(dialect=mysql.dialect()))
    assert compiled.startswith("UPDATE mysql_contract_items SET")
    assert "RETURNING" not in compiled


def test_mysql_joined_update_and_delete_fail_before_invalid_multi_table_dml():
    session = FakeMySQLSession()
    related = qs(session).filter(parent__name="Ada")
    with pytest.raises(NotImplementedError, match="joined relationship filters"):
        run(related.update(payload={"x": 2}))
    with pytest.raises(NotImplementedError, match="joined relationship filters"):
        run(related.delete())
    assert session.commands == []
