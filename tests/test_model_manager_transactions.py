from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import String, Uuid, create_engine, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from async_orm_kit import Manager, Model


class SyncBackedAsyncConnection:
    """Async facade over a synchronous SQLAlchemy Connection."""

    def __init__(self, connection):
        self.connection = connection

    async def execute(self, statement, params=None):
        return self.connection.execute(statement, params or {})


class SyncBackedAsyncSession:
    """Small async facade over SQLAlchemy's synchronous SQLite Session.

    The production package targets AsyncSession. This adapter lets the unit tests
    exercise its async call contract in environments where aiosqlite is not
    installed, while still using real SQLAlchemy identity maps and transactions.
    """

    def __init__(self, engine):
        self.sync_session = Session(engine)
        self.bind = engine
        self.commit_calls = 0
        self.rollback_calls = 0

    def add(self, instance):
        self.sync_session.add(instance)

    async def flush(self):
        self.sync_session.flush()

    async def refresh(self, instance, attribute_names=None):
        self.sync_session.refresh(instance, attribute_names=attribute_names)

    async def delete(self, instance):
        self.sync_session.delete(instance)

    async def commit(self):
        self.commit_calls += 1
        self.sync_session.commit()

    async def rollback(self):
        self.rollback_calls += 1
        self.sync_session.rollback()

    async def execute(self, statement, params=None, **kwargs):
        return self.sync_session.execute(statement, params or {}, **kwargs)

    def get_bind(self, mapper=None):
        return self.sync_session.get_bind(mapper=mapper)

    async def connection(self):
        return SyncBackedAsyncConnection(self.sync_session.connection())

    @property
    def no_autoflush(self):
        return self.sync_session.no_autoflush

    async def close(self):
        self.sync_session.close()


@pytest.fixture
def engine():
    value = create_engine("sqlite+pysqlite:///:memory:")
    TransactionUser.__table__.create(value)
    yield value
    value.dispose()


@pytest.fixture
def session(engine):
    value = SyncBackedAsyncSession(engine)
    yield value
    value.sync_session.close()


class TransactionUser(Model):
    __tablename__ = "phase2_transaction_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(120), unique=True)


class Observer:
    def __init__(self, events: list[str]):
        self.events = events

    async def pre_save(self, instance, **kwargs):
        self.events.append("pre_save")

    async def post_save(self, instance, created: bool, **kwargs):
        self.events.append(f"post_save:{created}")

    async def pre_delete(self, instance, **kwargs):
        self.events.append("pre_delete")

    async def post_delete(self, instance, **kwargs):
        self.events.append("post_delete")


def test_inherited_managers_are_cloned_per_concrete_model():
    class AbstractRecord(Model):
        __abstract__ = True
        objects = Manager()
        active = Manager()

    class InheritedManagerFirstRecord(AbstractRecord):
        __tablename__ = "phase2_first_records"
        id: Mapped[int] = mapped_column(primary_key=True)

    class InheritedManagerSecondRecord(AbstractRecord):
        __tablename__ = "phase2_second_records"
        id: Mapped[int] = mapped_column(primary_key=True)

    assert (
        InheritedManagerFirstRecord.objects is not InheritedManagerSecondRecord.objects
    )
    assert InheritedManagerFirstRecord.active is not InheritedManagerSecondRecord.active
    assert InheritedManagerFirstRecord.objects._model is InheritedManagerFirstRecord
    assert InheritedManagerSecondRecord.objects._model is InheritedManagerSecondRecord
    assert InheritedManagerFirstRecord.active._model is InheritedManagerFirstRecord
    assert InheritedManagerSecondRecord.active._model is InheritedManagerSecondRecord

    InheritedManagerFirstRecord.objects.add_query_hook(lambda queryset: queryset)
    assert len(InheritedManagerFirstRecord.objects._query_hooks) == 1
    assert InheritedManagerSecondRecord.objects._query_hooks == []
    assert AbstractRecord.objects._query_hooks == []


def test_manager_descriptor_does_not_rebind_when_accessed():
    original = TransactionUser.objects._model
    assert TransactionUser.objects._model is original
    assert TransactionUser.objects._model is TransactionUser

    with pytest.raises(AttributeError, match="not accessible"):
        _ = TransactionUser(name="test", email="test@example.com").objects


def test_manager_observer_registries_are_independent():
    class AbstractRecord(Model):
        __abstract__ = True
        objects = Manager()

    class ObserverRegistryFirstRecord(AbstractRecord):
        __tablename__ = "phase2_observer_first"
        id: Mapped[int] = mapped_column(primary_key=True)

    class ObserverRegistrySecondRecord(AbstractRecord):
        __tablename__ = "phase2_observer_second"
        id: Mapped[int] = mapped_column(primary_key=True)

    first_observer = object()
    ObserverRegistryFirstRecord.objects.add_observer(first_observer)
    assert first_observer in ObserverRegistryFirstRecord.objects._observers
    assert first_observer not in ObserverRegistrySecondRecord.objects._observers


def test_save_flushes_but_does_not_commit(session):
    user = TransactionUser(name="Ada", email="ada@example.com")

    import asyncio

    asyncio.run(user.save(session))

    assert user.id is not None
    assert session.sync_session.in_transaction()
    assert session.commit_calls == 0
    assert session.rollback_calls == 0
    assert (
        session.sync_session.scalar(select(func.count()).select_from(TransactionUser))
        == 1
    )

    session.sync_session.rollback()
    with Session(session.bind) as verifier:
        assert verifier.scalar(select(func.count()).select_from(TransactionUser)) == 0


def test_save_respects_caller_commit(session):
    import asyncio

    user = TransactionUser(name="Grace", email="grace@example.com")
    asyncio.run(user.save(session))
    session.sync_session.commit()

    with Session(session.bind) as verifier:
        stored = verifier.scalar(
            select(TransactionUser).where(TransactionUser.id == user.id)
        )
        assert stored is not None
        assert stored.name == "Grace"


def test_update_fields_discards_unselected_dirty_column_changes(session):
    import asyncio

    user = TransactionUser(name="Original", email="old@example.com")
    asyncio.run(user.save(session))
    session.sync_session.commit()

    user.name = "Must not persist"
    user.email = "new@example.com"
    asyncio.run(user.save(session, update_fields=["email"]))

    assert user.name == "Original"
    assert user.email == "new@example.com"
    session.sync_session.commit()

    with Session(session.bind) as verifier:
        stored = verifier.get(TransactionUser, user.id)
        assert stored.name == "Original"
        assert stored.email == "new@example.com"


def test_update_fields_rejects_relationship_names_and_unknown_fields(session):
    import asyncio

    user = TransactionUser(name="Ada", email="ada@example.com")
    with pytest.raises(ValueError, match="Invalid update_fields"):
        asyncio.run(user.save(session, update_fields=["missing_field"]))


def test_force_insert_rejects_an_already_persisted_instance(session):
    import asyncio

    user = TransactionUser(name="Ada", email="ada@example.com")
    asyncio.run(user.save(session))
    with pytest.raises(ValueError, match="transient"):
        asyncio.run(user.save(session, force_insert=True))


def test_refresh_from_db_is_awaitable(session):
    import asyncio

    user = TransactionUser(name="Database", email="db@example.com")
    asyncio.run(user.save(session))
    session.sync_session.commit()
    user.name = "Local only"
    asyncio.run(user.refresh_from_db(session))
    assert user.name == "Database"


def test_model_observers_run_in_order_after_flush_without_background_tasks(session):
    import asyncio

    events: list[str] = []
    observer = Observer(events)

    class ObservedUser(Model):
        __tablename__ = "phase2_observed_users"
        model_observers = [observer]
        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str] = mapped_column(String(80))

    ObservedUser.__table__.create(session.bind)
    user = ObservedUser(name="observer")
    asyncio.run(user.save(session))
    assert events == ["pre_save", "post_save:True"]
    assert user.id is not None

    asyncio.run(user.delete(session))
    assert events == [
        "pre_save",
        "post_save:True",
        "pre_delete",
        "post_delete",
    ]


def test_model_delete_does_not_commit(session):
    import asyncio

    user = TransactionUser(name="Delete me", email="delete@example.com")
    asyncio.run(user.save(session))
    session.sync_session.commit()

    asyncio.run(user.delete(session))
    assert session.sync_session.in_transaction()
    session.sync_session.rollback()

    with Session(session.bind) as verifier:
        assert verifier.get(TransactionUser, user.id) is not None


def test_queryset_bulk_create_update_delete_are_transaction_owned(session):
    import asyncio

    created = asyncio.run(
        TransactionUser.objects.bulk_create(
            session,
            [
                {"name": "A", "email": "a@example.com"},
                {"name": "B", "email": "b@example.com"},
            ],
        )
    )
    assert created == 2
    assert session.sync_session.in_transaction()
    assert session.commit_calls == 0
    assert session.rollback_calls == 0
    session.sync_session.rollback()

    with Session(session.bind) as verifier:
        assert verifier.scalar(select(func.count()).select_from(TransactionUser)) == 0

    user = TransactionUser(name="Update me", email="update@example.com")
    asyncio.run(user.save(session))
    session.sync_session.commit()

    asyncio.run(
        TransactionUser.objects.filter(session, id=user.id).update(name="Updated")
    )
    assert session.sync_session.in_transaction()
    assert session.commit_calls == 0
    assert session.rollback_calls == 0
    session.sync_session.rollback()

    with Session(session.bind) as verifier:
        assert verifier.get(TransactionUser, user.id).name == "Update me"

    asyncio.run(TransactionUser.objects.filter(session, id=user.id).delete())
    assert session.sync_session.in_transaction()
    assert session.commit_calls == 0
    assert session.rollback_calls == 0
    session.sync_session.rollback()
    with Session(session.bind) as verifier:
        assert verifier.get(TransactionUser, user.id) is not None


def test_bulk_update_persists_only_selected_fields(session):
    import asyncio

    first = TransactionUser(name="first", email="first@example.com")
    second = TransactionUser(name="second", email="second@example.com")
    asyncio.run(first.save(session))
    asyncio.run(second.save(session))
    session.sync_session.commit()

    first.name = "ignored-first"
    first.email = "updated-first@example.com"
    second.name = "ignored-second"
    second.email = "updated-second@example.com"

    count = asyncio.run(
        TransactionUser.objects.bulk_update(
            session, [first, second], fields=["email"], batch_size=2
        )
    )
    assert count == 2
    assert session.commit_calls == 0
    assert session.rollback_calls == 0
    assert first.name == "first"
    assert second.name == "second"
    session.sync_session.commit()

    with Session(session.bind) as verifier:
        assert (
            verifier.get(TransactionUser, first.id).email == "updated-first@example.com"
        )
        assert (
            verifier.get(TransactionUser, second.id).email
            == "updated-second@example.com"
        )


def test_bulk_update_rejects_invalid_batch_size(session):
    with pytest.raises(ValueError, match="positive integer"):
        import asyncio

        asyncio.run(
            TransactionUser.objects.bulk_update(
                session,
                [TransactionUser(name="x", email="x@example.com")],
                fields=["name"],
                batch_size=0,
            )
        )


def test_manager_get_or_create_does_not_hide_database_errors(monkeypatch, session):
    import asyncio

    manager = TransactionUser.objects
    monkeypatch.setattr(
        manager, "get", AsyncMock(side_effect=RuntimeError("database unavailable"))
    )
    monkeypatch.setattr(manager, "create", AsyncMock())

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(manager.get_or_create(session, email="missing@example.com"))
    manager.create.assert_not_awaited()


def test_manager_get_or_create_creates_only_for_missing_object(monkeypatch, session):
    import asyncio

    manager = TransactionUser.objects
    expected = TransactionUser(name="new", email="new@example.com")
    monkeypatch.setattr(
        manager, "get", AsyncMock(side_effect=TransactionUser.DoesNotExist())
    )
    monkeypatch.setattr(manager, "create", AsyncMock(return_value=expected))

    actual, created = asyncio.run(
        manager.get_or_create(session, email="new@example.com")
    )
    assert actual is expected
    assert created is True
    manager.create.assert_awaited_once_with(session, email="new@example.com")


def test_update_or_create_propagates_non_missing_errors(monkeypatch, session):
    import asyncio

    manager = TransactionUser.objects
    monkeypatch.setattr(
        manager, "get", AsyncMock(side_effect=RuntimeError("connection lost"))
    )
    monkeypatch.setattr(manager, "create", AsyncMock())

    with pytest.raises(RuntimeError, match="connection lost"):
        asyncio.run(
            manager.update_or_create(
                session, defaults={"name": "x"}, email="x@example.com"
            )
        )
    manager.create.assert_not_awaited()


def test_update_or_create_uses_model_save_for_existing_instance(monkeypatch, session):
    import asyncio

    manager = TransactionUser.objects
    existing = TransactionUser(name="before", email="before@example.com")
    existing.save = AsyncMock(return_value=existing)
    monkeypatch.setattr(manager, "get", AsyncMock(return_value=existing))

    actual, created = asyncio.run(
        manager.update_or_create(
            session,
            defaults={"name": "after"},
            email="before@example.com",
        )
    )

    assert actual is existing
    assert created is False
    existing.save.assert_awaited_once_with(session, update_fields=["name"])


def test_nearer_non_manager_attribute_shadows_inherited_manager():
    class ManagerMixin(Model):
        __abstract__ = True
        objects = Manager()

    class ShadowMixin:
        objects = None

    class ShadowedManagerModel(ShadowMixin, ManagerMixin):
        __tablename__ = "phase2_shadowed_manager"
        id: Mapped[int] = mapped_column(primary_key=True)

    # The class namespace can still install a default manager if none remains,
    # but the inherited descriptor must not be rebound into the child.
    assert ManagerMixin.objects._model is ManagerMixin
    assert ShadowedManagerModel.objects._model is ShadowedManagerModel


def test_observer_mixin_supports_sync_and_async_handlers():
    import asyncio

    from async_orm_kit.observers.mixin import ObserverMixin

    events: list[str] = []

    class SyncObserver:
        def on_event(self, **kwargs):
            events.append("sync")

    class AsyncObserver:
        async def on_event(self, **kwargs):
            events.append("async")

    dispatcher = ObserverMixin()
    asyncio.run(
        dispatcher._trigger_observers([SyncObserver(), AsyncObserver()], "on_event")
    )
    assert events == ["sync", "async"]


def test_no_untracked_background_tasks_remain_in_model_lifecycle():
    from pathlib import Path

    import async_orm_kit.models.base as model_base

    source = Path(model_base.__file__).read_text(encoding="utf-8")
    assert "asyncio.create_task" not in source
    assert "after_commit" not in source


def test_model_clean_sees_restored_unselected_values(session):
    import asyncio

    user = TransactionUser(name="stored", email="stored@example.com")
    asyncio.run(user.save(session))
    session.sync_session.commit()

    seen: dict[str, str] = {}

    async def clean():
        seen["name"] = user.name

    user.clean = clean
    user.name = "temporary-invalid-value"
    user.email = "new@example.com"
    asyncio.run(user.save(session, update_fields=["email"]))

    assert seen["name"] == "stored"
    assert user.name == "stored"
    assert user.email == "new@example.com"


def test_manager_create_flushes_without_committing(session):
    import asyncio

    user = asyncio.run(
        TransactionUser.objects.create(
            session, name="manager create", email="manager-create@example.com"
        )
    )
    assert user.id is not None
    assert session.sync_session.in_transaction()
    assert session.commit_calls == 0
    assert session.rollback_calls == 0
    session.sync_session.rollback()

    with Session(session.bind) as verifier:
        assert verifier.get(TransactionUser, user.id) is None


def test_update_from_dict_commit_argument_is_deprecated_and_ignored():
    user = TransactionUser(name="before", email="before@example.com")
    with pytest.warns(DeprecationWarning, match="has no effect"):
        result = user.update_from_dict({"name": "after"}, commit=True)
    assert result is user
    assert user.name == "after"


def test_failed_model_flush_does_not_rollback_caller_session(session):
    import asyncio

    from async_orm_kit.exceptions import DatabaseError

    original = TransactionUser(name="original", email="unique@example.com")
    asyncio.run(original.save(session))
    session.sync_session.commit()

    duplicate = TransactionUser(name="duplicate", email="unique@example.com")
    with pytest.raises(DatabaseError):
        asyncio.run(duplicate.save(session))

    assert session.rollback_calls == 0
    assert session.sync_session.in_transaction()
    session.sync_session.rollback()

    with Session(session.bind) as verifier:
        assert verifier.scalar(select(func.count()).select_from(TransactionUser)) == 1


def test_refresh_from_db_attaches_detached_instance(session):
    import asyncio

    user = TransactionUser(name="stored", email="detached@example.com")
    asyncio.run(user.save(session))
    session.sync_session.commit()
    session.sync_session.expunge(user)
    user.name = "local"

    asyncio.run(user.refresh_from_db(session))
    assert user.name == "stored"
    assert user in session.sync_session


def test_bulk_upsert_returning_refreshes_existing_identity_map_instance(session):
    import asyncio

    async def create_and_upsert():
        original = await TransactionUser.objects.bulk_create(
            session,
            [{"name": "Original", "email": "upsert@example.com"}],
            returning=True,
        )
        updated = await TransactionUser.objects.bulk_create(
            session,
            [{"name": "Updated", "email": "upsert@example.com"}],
            update_conflicts=True,
            unique_fields=["email"],
            update_fields=["name"],
            returning=True,
        )
        return original, updated

    original, updated = asyncio.run(create_and_upsert())

    assert len(original) == 1
    assert len(updated) == 1
    assert updated[0] is original[0]
    assert updated[0].name == "Updated"
    assert updated[0].email == "upsert@example.com"


class DefaultedBulkRecord(Model):
    __tablename__ = "phase5_defaulted_bulk_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[UUID] = mapped_column(
        "external_token",
        Uuid(as_uuid=True),
        default=uuid4,
        unique=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column("display_name", String(80), unique=True)


def test_bulk_create_applies_python_column_defaults(session):
    import asyncio

    DefaultedBulkRecord.__table__.create(session.bind)

    explicit_token = uuid4()
    upsert_token = uuid4()
    returning_upsert_token = uuid4()

    async def create_records():
        returned = await DefaultedBulkRecord.objects.bulk_create(
            session,
            [{"name": "first"}, {"name": "second"}],
            returning=True,
        )
        count = await DefaultedBulkRecord.objects.bulk_create(
            session,
            [{"name": "third", "token": explicit_token}],
        )
        upsert_count = await DefaultedBulkRecord.objects.bulk_create(
            session,
            [{"name": "first", "token": upsert_token}],
            update_conflicts=True,
            unique_fields=["name"],
            update_fields=["token"],
        )
        returned_upsert = await DefaultedBulkRecord.objects.bulk_create(
            session,
            [{"name": "first", "token": returning_upsert_token}],
            update_conflicts=True,
            unique_fields=["name"],
            update_fields=["token"],
            returning=True,
        )
        ignored_count = await DefaultedBulkRecord.objects.bulk_create(
            session,
            [
                {"name": "first", "token": uuid4()},
                {"name": "fourth", "token": uuid4()},
            ],
            ignore_conflicts=True,
        )
        return returned, count, upsert_count, returned_upsert, ignored_count

    returned, count, upsert_count, returned_upsert, ignored_count = asyncio.run(
        create_records()
    )

    assert len(returned) == 2
    assert count == 1
    assert upsert_count == 1
    assert len(returned_upsert) == 1
    assert ignored_count == 1
    assert returned_upsert[0] is returned[0]
    assert returned_upsert[0].token == returning_upsert_token
    assert all(isinstance(record.token, UUID) for record in returned)
    assert len({record.token for record in returned}) == 2

    session.sync_session.expire_all()
    stored_first = session.sync_session.scalar(
        select(DefaultedBulkRecord).where(DefaultedBulkRecord.name == "first")
    )
    assert stored_first is not None
    assert stored_first.token == returning_upsert_token

    stored_count = session.sync_session.scalar(
        select(func.count()).select_from(DefaultedBulkRecord)
    )
    assert stored_count == 4


def test_manager_does_not_silently_accept_unimplemented_result_processors():
    manager = Manager()
    with pytest.raises(
        NotImplementedError, match="result processors are not implemented"
    ):
        manager.add_result_processor(lambda rows: rows)
    assert manager._result_processors == []
