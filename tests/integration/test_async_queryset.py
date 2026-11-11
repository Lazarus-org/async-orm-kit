from __future__ import annotations

import pytest
import pytest_asyncio

pytest.importorskip("aiosqlite")

from sqlalchemy import (
    Column,
    ForeignKey,
    String,
    Table,
    event,
    func,
    select,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column, relationship

from async_orm_kit import Model
from async_orm_kit.exceptions import DatabaseError

phase5_async_post_tags = Table(
    "phase5_async_post_tags",
    Model.metadata,
    Column("post_id", ForeignKey("phase5_async_posts.id"), primary_key=True),
    Column("tag_id", ForeignKey("phase5_async_tags.id"), primary_key=True),
)


class Phase5AsyncAuthor(Model):
    __tablename__ = "phase5_async_authors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)

    profile: Mapped[Phase5AsyncProfile | None] = relationship(
        back_populates="author",
        uselist=False,
        lazy="raise",
    )
    posts: Mapped[list[Phase5AsyncPost]] = relationship(
        back_populates="author",
        lazy="raise",
    )


class Phase5AsyncProfile(Model):
    __tablename__ = "phase5_async_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    bio: Mapped[str] = mapped_column(String(200))
    author_id: Mapped[int] = mapped_column(
        ForeignKey("phase5_async_authors.id"),
        unique=True,
    )

    author: Mapped[Phase5AsyncAuthor] = relationship(
        back_populates="profile",
        lazy="raise",
    )


class Phase5AsyncPost(Model):
    __tablename__ = "phase5_async_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    author_id: Mapped[int] = mapped_column(ForeignKey("phase5_async_authors.id"))

    author: Mapped[Phase5AsyncAuthor] = relationship(
        back_populates="posts",
        lazy="raise",
    )
    comments: Mapped[list[Phase5AsyncComment]] = relationship(
        back_populates="post",
        lazy="raise",
    )
    tags: Mapped[list[Phase5AsyncTag]] = relationship(
        secondary=phase5_async_post_tags,
        back_populates="posts",
        lazy="raise",
    )


class Phase5AsyncComment(Model):
    __tablename__ = "phase5_async_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    body: Mapped[str] = mapped_column(String(200))
    post_id: Mapped[int] = mapped_column(ForeignKey("phase5_async_posts.id"))

    post: Mapped[Phase5AsyncPost] = relationship(
        back_populates="comments",
        lazy="raise",
    )


class Phase5AsyncTag(Model):
    __tablename__ = "phase5_async_tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)

    posts: Mapped[list[Phase5AsyncPost]] = relationship(
        secondary=phase5_async_post_tags,
        back_populates="tags",
        lazy="raise",
    )


@pytest_asyncio.fixture
async def async_query_environment(tmp_path):
    database_path = tmp_path / "async-queryset.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    tables = [
        Phase5AsyncAuthor.__table__,
        Phase5AsyncProfile.__table__,
        Phase5AsyncPost.__table__,
        Phase5AsyncComment.__table__,
        Phase5AsyncTag.__table__,
        phase5_async_post_tags,
    ]

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Model.metadata.create_all(
                sync_connection,
                tables=tables,
            )
        )

    statements: list[str] = []

    def record_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        ada = Phase5AsyncAuthor(
            name="Ada",
            profile=Phase5AsyncProfile(bio="Mathematician"),
            posts=[
                Phase5AsyncPost(
                    title="Analytical Engine",
                    comments=[
                        Phase5AsyncComment(body="First comment"),
                        Phase5AsyncComment(body="Second comment"),
                    ],
                ),
                Phase5AsyncPost(title="Computing"),
            ],
        )
        grace = Phase5AsyncAuthor(
            name="Grace",
            profile=Phase5AsyncProfile(bio="Compiler pioneer"),
            posts=[
                Phase5AsyncPost(
                    title="COBOL",
                    comments=[Phase5AsyncComment(body="Third comment")],
                )
            ],
        )
        linus = Phase5AsyncAuthor(name="Linus")
        history = Phase5AsyncTag(name="history")
        ada.posts[0].tags.append(history)
        session.add_all([ada, grace, linus])
        await session.commit()

    statements.clear()
    try:
        yield factory, statements
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record_statement)
        await engine.dispose()


@pytest.mark.asyncio
async def test_query_set_reads_use_a_real_async_session(async_query_environment):
    factory, _statements = async_query_environment

    async with factory() as session:
        authors = await Phase5AsyncAuthor.objects.filter(
            session,
            name__icontains="a",
        ).order_by("name")

        assert [author.name for author in authors] == ["Ada", "Grace"]

        first = await Phase5AsyncAuthor.objects.filter(
            session,
            name="Ada",
        ).first()
        assert first is not None
        assert first.name == "Ada"

        found = await Phase5AsyncAuthor.objects.filter(
            session,
            name="Grace",
        ).get()
        assert found.name == "Grace"

        assert (
            await Phase5AsyncAuthor.objects.filter(
                session,
                name__startswith="A",
            ).count()
            == 1
        )
        assert await Phase5AsyncAuthor.objects.filter(
            session,
            name="Linus",
        ).exists()
        assert not await Phase5AsyncAuthor.objects.filter(
            session,
            name="missing",
        ).exists()

        values = await Phase5AsyncAuthor.objects.values(
            session,
            "name",
        ).order_by("name")
        assert values == [
            {"name": "Ada"},
            {"name": "Grace"},
            {"name": "Linus"},
        ]

        names = await Phase5AsyncAuthor.objects.values_list(
            session,
            "name",
            flat=True,
        ).order_by("name")
        assert names == ["Ada", "Grace", "Linus"]


@pytest.mark.asyncio
async def test_query_set_stream_supports_async_for_and_explicit_stream(
    async_query_environment,
):
    factory, _statements = async_query_environment

    async with factory() as session:
        query = Phase5AsyncAuthor.objects.all(session).order_by("id")

        via_async_for = []
        async for author in query:
            via_async_for.append(author.name)

        via_stream_method = []
        async for author in query.stream():
            via_stream_method.append(author.name)

        assert via_async_for == ["Ada", "Grace", "Linus"]
        assert via_stream_method == via_async_for


@pytest.mark.asyncio
async def test_stream_can_be_closed_early_without_leaking_session_resources(
    async_query_environment,
):
    factory, _statements = async_query_environment

    async with factory() as session:
        iterator = Phase5AsyncAuthor.objects.all(session).order_by("id").stream()
        first = await anext(iterator)
        assert first.name == "Ada"

        await iterator.aclose()

        # The same AsyncSession remains usable after the streamed cursor is closed.
        count = await session.scalar(
            select(func.count()).select_from(Phase5AsyncAuthor)
        )
        assert count == 3


@pytest.mark.asyncio
async def test_select_related_loads_scalar_relationship_in_one_query(
    async_query_environment,
):
    factory, statements = async_query_environment

    async with factory() as session:
        statements.clear()
        posts = await Phase5AsyncPost.objects.select_related(
            session,
            "author",
        ).order_by("id")

        select_statements = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith("SELECT")
        ]
        assert len(select_statements) == 1
        assert [post.author.name for post in posts] == ["Ada", "Ada", "Grace"]
        assert len(select_statements) == 1


@pytest.mark.asyncio
async def test_nested_prefetch_loads_collections_without_n_plus_one(
    async_query_environment,
):
    factory, statements = async_query_environment

    async with factory() as session:
        statements.clear()
        authors = await Phase5AsyncAuthor.objects.prefetch_related(
            session,
            "posts__comments",
        ).order_by("id")

        select_statements = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith("SELECT")
        ]
        assert len(select_statements) == 3

        comment_bodies = [
            comment.body
            for author in authors
            for post in author.posts
            for comment in post.comments
        ]
        assert comment_bodies == [
            "First comment",
            "Second comment",
            "Third comment",
        ]

        # Accessing all nested collections must not issue additional SELECTs.
        assert (
            len(
                [
                    statement
                    for statement in statements
                    if statement.lstrip().upper().startswith("SELECT")
                ]
            )
            == 3
        )


@pytest.mark.asyncio
async def test_nested_many_to_many_prefetch_uses_async_session(
    async_query_environment,
):
    factory, statements = async_query_environment

    async with factory() as session:
        statements.clear()
        posts = await Phase5AsyncPost.objects.prefetch_related(
            session,
            "tags",
        ).order_by("id")

        assert [tag.name for tag in posts[0].tags] == ["history"]
        assert posts[1].tags == []
        assert (
            len(
                [
                    statement
                    for statement in statements
                    if statement.lstrip().upper().startswith("SELECT")
                ]
            )
            == 2
        )


@pytest.mark.asyncio
async def test_query_set_mutations_remain_inside_caller_transaction(
    async_query_environment,
):
    factory, _statements = async_query_environment

    async with factory() as session:
        async with session.begin():
            created = await Phase5AsyncAuthor.objects.create(
                session,
                name="New author",
            )
            assert created.id is not None

            returned = await Phase5AsyncAuthor.objects.bulk_create(
                session,
                [
                    {"name": "Bulk author one"},
                    {"name": "Bulk author two"},
                ],
                returning=True,
            )
            assert {author.name for author in returned} == {
                "Bulk author one",
                "Bulk author two",
            }

            updated = await Phase5AsyncAuthor.objects.filter(
                session,
                name="New author",
            ).update(name="Updated author", returning=True)
            assert len(updated) == 1
            assert updated[0].name == "Updated author"

            deleted = await Phase5AsyncAuthor.objects.filter(
                session,
                name="Bulk author one",
            ).delete()
            assert deleted == 1

    async with factory() as verifier:
        names = await Phase5AsyncAuthor.objects.values_list(
            verifier,
            "name",
            flat=True,
        ).order_by("name")
        assert "Updated author" in names
        assert "Bulk author one" not in names
        assert "Bulk author two" in names


@pytest.mark.asyncio
async def test_query_set_mutations_do_not_commit_caller_owned_transaction(
    async_query_environment,
):
    factory, _statements = async_query_environment

    async with factory() as session:
        await session.begin()
        await Phase5AsyncAuthor.objects.create(
            session,
            name="Must roll back",
        )
        await session.rollback()

    async with factory() as verifier:
        assert not await Phase5AsyncAuthor.objects.filter(
            verifier,
            name="Must roll back",
        ).exists()


@pytest.mark.asyncio
async def test_collection_join_count_counts_distinct_root_models(
    async_query_environment,
):
    factory, _statements = async_query_environment

    async with factory() as session:
        count = await Phase5AsyncPost.objects.filter(
            session,
            comments__body__icontains="comment",
        ).count()
        assert count == 2


@pytest.mark.asyncio
async def test_explain_uses_the_async_connection(async_query_environment):
    factory, _statements = async_query_environment

    async with factory() as session:
        plan = await Phase5AsyncAuthor.objects.filter(
            session,
            name="Ada",
        ).explain()
        assert "SEARCH" in plan.upper() or "SCAN" in plan.upper()


@pytest.mark.asyncio
async def test_database_errors_preserve_the_original_exception(
    async_query_environment,
):
    factory, _statements = async_query_environment

    async with factory() as session:
        with pytest.raises(DatabaseError) as error:
            await Phase5AsyncAuthor.objects.create(
                session,
                name="Ada",
            )

        assert error.value.__cause__ is not None
        await session.rollback()


@pytest.mark.asyncio
async def test_advanced_counts_and_exclusions_on_real_async_session(
    async_query_environment,
):
    """Execute Stage 1 relationship/aggregate semantics with a real async driver."""
    from async_orm_kit import Count, Q

    factory, _statements = async_query_environment
    async with factory() as session:
        authors = await Phase5AsyncAuthor.objects.annotate(
            session, post_count=Count("posts")
        ).order_by("id")
        assert [(author.name, author.post_count) for author in authors] == [
            ("Ada", 2),
            ("Grace", 1),
            ("Linus", 0),
        ]
        excluded = await Phase5AsyncAuthor.objects.exclude(
            session, posts__title="Analytical Engine"
        ).order_by("id")
        assert [author.name for author in excluded] == ["Grace", "Linus"]
        negated = await Phase5AsyncAuthor.objects.filter(
            session, ~Q(posts__title="Analytical Engine")
        ).order_by("id")
        assert [author.name for author in negated] == ["Grace", "Linus"]


@pytest.mark.asyncio
async def test_advanced_window_and_atomic_function_update_with_real_async_session(
    async_query_environment,
):
    from async_orm_kit import Concat, F, RowNumber, Value, Window

    factory, _statements = async_query_environment
    async with factory() as session:
        updated = await Phase5AsyncPost.objects.filter(
            session, title="Computing"
        ).update(title=Concat(F("title"), Value("!")))
        assert updated == 1
        rows = await Phase5AsyncPost.objects.annotate(
            session,
            position=Window(RowNumber(), order_by=[F("id").asc()]),
        ).order_by("id")
        assert [item.position for item in rows] == [1, 2, 3]
        assert "Computing!" in [item.title for item in rows]
        await session.rollback()
