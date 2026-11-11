from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import Depends, FastAPI

from async_orm_kit.exceptions import (
    DatabaseError,
    MultipleObjectsReturned,
    ObjectDoesNotExist,
)
from async_orm_kit.integrations.fastapi import (
    create_session_dependency,
    register_exception_handlers,
)


class FakeSession:
    def __init__(self, *, active_transaction: bool = True) -> None:
        self.active_transaction = active_transaction
        self.rollback_calls = 0
        self.closed = False
        self.token = f"session-{id(self)}"

    def in_transaction(self) -> bool:
        return self.active_transaction

    async def rollback(self) -> None:
        self.rollback_calls += 1
        self.active_transaction = False

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.closed = True


class FakeSessionFactory:
    def __init__(self) -> None:
        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        session = FakeSession()
        self.sessions.append(session)
        return session


def test_session_dependency_yields_and_closes_a_fresh_session():
    factory = FakeSessionFactory()
    dependency = create_session_dependency(factory)

    async def run():
        generator = dependency()
        session = await anext(generator)
        with pytest.raises(StopAsyncIteration):
            await anext(generator)
        return session

    session = asyncio.run(run())
    assert session is factory.sessions[0]
    assert session.closed is True
    assert session.rollback_calls == 0


def test_session_dependency_rolls_back_when_request_raises():
    factory = FakeSessionFactory()
    dependency = create_session_dependency(factory)

    async def run():
        generator = dependency()
        session = await anext(generator)
        with pytest.raises(RuntimeError, match="endpoint failed"):
            await generator.athrow(RuntimeError("endpoint failed"))
        return session

    session = asyncio.run(run())
    assert session.rollback_calls == 1
    assert session.closed is True


def test_session_dependency_rolls_back_on_cancellation():
    factory = FakeSessionFactory()
    dependency = create_session_dependency(factory)

    async def run():
        generator = dependency()
        session = await anext(generator)
        with pytest.raises(asyncio.CancelledError):
            await generator.athrow(asyncio.CancelledError())
        return session

    session = asyncio.run(run())
    assert session.rollback_calls == 1
    assert session.closed is True


def test_session_dependency_does_not_rollback_when_no_transaction_exists():
    factory = FakeSessionFactory()
    dependency = create_session_dependency(factory)

    async def run():
        generator = dependency()
        session = await anext(generator)
        session.active_transaction = False
        with pytest.raises(ValueError):
            await generator.athrow(ValueError("request failed"))
        return session

    session = asyncio.run(run())
    assert session.rollback_calls == 0
    assert session.closed is True


def test_session_dependency_requires_a_callable_factory():
    with pytest.raises(TypeError, match="must be callable"):
        create_session_dependency(None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_fastapi_dependency_injects_one_session_per_request():
    factory = FakeSessionFactory()
    dependency = create_session_dependency(factory)
    app = FastAPI()

    @app.get("/session")
    async def session_route(session=Depends(dependency)):
        return {"token": session.token}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/session")
        second = await client.get("/session")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["token"] != second.json()["token"]
    assert len(factory.sessions) == 2
    assert all(session.closed for session in factory.sessions)


@pytest.mark.asyncio
async def test_fastapi_dependency_rolls_back_when_endpoint_fails():
    factory = FakeSessionFactory()
    dependency = create_session_dependency(factory)
    app = FastAPI()

    @app.get("/failure")
    async def failing_route(session=Depends(dependency)):
        raise RuntimeError("endpoint failed")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/failure")

    assert response.status_code == 500
    assert len(factory.sessions) == 1
    assert factory.sessions[0].rollback_calls == 1
    assert factory.sessions[0].closed is True


@pytest.mark.asyncio
async def test_exception_handlers_map_not_found_and_multiple_results_safely():
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/missing")
    async def missing():
        raise ObjectDoesNotExist("internal model detail")

    @app.get("/ambiguous")
    async def ambiguous():
        raise MultipleObjectsReturned("internal query detail")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing_response = await client.get("/missing")
        ambiguous_response = await client.get("/ambiguous")

    assert missing_response.status_code == 404
    assert missing_response.json() == {"detail": "Resource not found"}
    assert ambiguous_response.status_code == 409
    assert "internal" not in ambiguous_response.text


@pytest.mark.asyncio
async def test_database_exception_handler_does_not_disclose_database_details():
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/database-error")
    async def database_error():
        raise DatabaseError(
            message="password=do-not-leak",
            cause=RuntimeError("postgres://user:secret@db/private"),
        )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/database-error")

    assert response.status_code == 500
    assert response.json() == {"detail": "A database operation failed"}
    assert "do-not-leak" not in response.text
    assert "secret" not in response.text


def test_register_exception_handlers_validates_application_argument():
    with pytest.raises(TypeError, match="FastAPI-compatible"):
        register_exception_handlers(object())
