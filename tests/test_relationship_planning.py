from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import (
    Column,
    ForeignKey,
    String,
    Table,
    create_engine,
    event,
    inspect as sa_inspect,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship
from sqlalchemy.orm.attributes import NO_VALUE

from async_orm_kit import Model
from async_orm_kit.config import ORMConfig
from async_orm_kit.exceptions import RelationshipError
from async_orm_kit.query.planning.directives import (
    LoadDirective,
    LoadStrategy,
    QueryContext,
)
from async_orm_kit.query.planning.factory import create_default_planner
from async_orm_kit.query.planning.passes.cartesian_detection import (
    CartesianDetectionPass,
)
from async_orm_kit.query.planning.passes.depth_splitting import DepthSplittingPass
from async_orm_kit.query.planning.passes.join_promotion import JoinPromotionPass
from async_orm_kit.query.planning.passes.path_conflicts import PathStrategyConflictPass
from async_orm_kit.query.planning.passes.redundant_path import RedundantPathPass
from async_orm_kit.query.relations.graph import JoinGraph, JoinType
from async_orm_kit.queryset.queryset import QuerySet

phase4_post_tags = Table(
    "phase4_post_tags",
    Model.metadata,
    Column("post_id", ForeignKey("phase4_posts.id"), primary_key=True),
    Column("tag_id", ForeignKey("phase4_tags.id"), primary_key=True),
)


class Phase4User(Model):
    __tablename__ = "phase4_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))

    profile: Mapped[Phase4Profile | None] = relationship(
        back_populates="user", uselist=False
    )
    posts: Mapped[list[Phase4Post]] = relationship(back_populates="author")


class Phase4Profile(Model):
    __tablename__ = "phase4_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    bio: Mapped[str] = mapped_column(String(200))
    user_id: Mapped[int] = mapped_column(ForeignKey("phase4_users.id"), unique=True)

    user: Mapped[Phase4User] = relationship(back_populates="profile")


class Phase4Post(Model):
    __tablename__ = "phase4_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(100))
    author_id: Mapped[int] = mapped_column(ForeignKey("phase4_users.id"))

    author: Mapped[Phase4User] = relationship(back_populates="posts")
    comments: Mapped[list[Phase4Comment]] = relationship(back_populates="post")
    tags: Mapped[list[Phase4Tag]] = relationship(
        secondary=phase4_post_tags, back_populates="posts"
    )


class Phase4Comment(Model):
    __tablename__ = "phase4_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    body: Mapped[str] = mapped_column(String(200))
    post_id: Mapped[int] = mapped_column(ForeignKey("phase4_posts.id"))

    post: Mapped[Phase4Post] = relationship(back_populates="comments")


class Phase4Tag(Model):
    __tablename__ = "phase4_tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40))

    posts: Mapped[list[Phase4Post]] = relationship(
        secondary=phase4_post_tags, back_populates="tags"
    )


class Phase4JoinedParent(Model):
    __tablename__ = "phase4_joined_parents"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    children: Mapped[list[Phase4JoinedChild]] = relationship(
        back_populates="parent", lazy="joined"
    )


class Phase4JoinedChild(Model):
    __tablename__ = "phase4_joined_children"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    parent_id: Mapped[int] = mapped_column(ForeignKey("phase4_joined_parents.id"))
    parent: Mapped[Phase4JoinedParent] = relationship(back_populates="children")


class SyncSessionAsyncAdapter:
    """Minimal async-shaped adapter for executing ORM statements in SQLite tests."""

    def __init__(self, session: Session):
        self._session = session
        self.bind = session.get_bind()

    def get_bind(self):
        return self.bind

    async def execute(self, statement, params=None):
        return self._session.execute(statement, params or {})

    async def close(self):
        self._session.close()


@pytest.fixture
def relationship_db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Model.metadata.create_all(
        engine,
        tables=[
            Phase4User.__table__,
            Phase4Profile.__table__,
            Phase4Post.__table__,
            Phase4Comment.__table__,
            Phase4Tag.__table__,
            phase4_post_tags,
            Phase4JoinedParent.__table__,
            Phase4JoinedChild.__table__,
        ],
    )

    with Session(engine) as seed:
        ada = Phase4User(name="Ada")
        ada.profile = Phase4Profile(bio="Mathematician")
        ada.posts = [
            Phase4Post(
                title="Analytical Engine",
                comments=[
                    Phase4Comment(body="First"),
                    Phase4Comment(body="Second"),
                ],
            ),
            Phase4Post(
                title="Computing",
                comments=[Phase4Comment(body="Third")],
            ),
        ]
        grace = Phase4User(name="Grace")
        grace.profile = Phase4Profile(bio="Compiler pioneer")
        grace.posts = [Phase4Post(title="COBOL")]
        tag = Phase4Tag(name="history")
        ada.posts[0].tags.append(tag)
        joined_parent = Phase4JoinedParent(
            name="joined-parent",
            children=[
                Phase4JoinedChild(name="child-a"),
                Phase4JoinedChild(name="child-b"),
            ],
        )
        seed.add_all([ada, grace, joined_parent])
        seed.commit()

    statements: list[str] = []

    def record_sql(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_sql)
    session = Session(engine)
    adapter = SyncSessionAsyncAdapter(session)
    try:
        yield engine, adapter, statements
    finally:
        session.close()
        event.remove(engine, "before_cursor_execute", record_sql)
        engine.dispose()


def run(queryset):
    return asyncio.run(queryset._fetch())


def test_join_graph_creates_valid_nested_tree_and_isolates_clones():
    graph = JoinGraph(Phase4User)
    leaf = graph.add_node("posts__comments", used_in_filter=True)

    assert [node.path for node in graph.join_order] == [
        "posts",
        "posts__comments",
    ]
    assert graph.paths == ("posts", "posts__comments")
    assert leaf.parent.path == "posts"
    assert graph.validate() == []

    cloned = graph.clone()
    assert cloned is not graph
    assert cloned.get_node("posts") is not graph.get_node("posts")
    assert cloned.get_node("posts").alias is graph.get_node("posts").alias
    cloned.set_join_type("posts", JoinType.INNER)
    assert graph.get_node("posts").join_type == JoinType.LEFT
    assert cloned.validate() == []


def test_join_graph_rejects_malformed_and_unknown_paths():
    graph = JoinGraph(Phase4User)

    with pytest.raises(ValueError, match="non-empty segments"):
        graph.add_node("posts____comments")

    with pytest.raises(RelationshipError, match="Invalid relationship path"):
        graph.add_node("posts__missing")

    with pytest.raises(ValueError, match="Invalid join type"):
        graph.add_node("posts", join_type="CROSS")


def test_join_graph_requires_parent_before_child_and_validates_order():
    graph = JoinGraph(Phase4User)
    graph.add_node("posts__comments")
    graph._join_order.reverse()

    errors = graph.validate()
    assert any("appears before its parent" in error for error in errors)


def test_join_graph_public_order_views_cannot_mutate_internal_collections():
    graph = JoinGraph(Phase4User)
    graph.add_node("posts")

    assert isinstance(graph.join_order, tuple)
    assert isinstance(graph.paths, tuple)
    with pytest.raises(AttributeError):
        graph.join_order.append(graph.get_node("posts"))


def test_select_related_rejects_collection_paths_and_invalid_relationships():
    query = QuerySet(Phase4User, session=object())

    with pytest.raises(RelationshipError, match="collection relationship"):
        query.select_related("posts")

    with pytest.raises(RelationshipError, match="collection relationship"):
        query.select_related("posts__comments")

    with pytest.raises(RelationshipError, match="Invalid relationship path"):
        query.select_related("does_not_exist")


def test_prefetch_related_accepts_collections_and_deduplicates_paths():
    query = QuerySet(Phase4User, session=object()).prefetch_related(
        "posts", "posts", "posts__comments"
    )

    assert query._state.prefetch_related == ["posts", "posts__comments"]


def test_select_related_and_prefetch_same_path_select_related_wins():
    query = (
        QuerySet(Phase4User, session=object())
        .prefetch_related("profile")
        .select_related("profile")
    )

    assert query._state.select_related == ["profile"]
    assert query._state.prefetch_related == []


def test_select_related_prefetch_related_execute_without_n_plus_one(relationship_db):
    _engine, adapter, statements = relationship_db

    users = run(QuerySet(Phase4User, adapter).select_related("profile"))
    assert len(users) == 2
    assert len(statements) == 1
    assert users[0].profile.bio == "Mathematician"
    assert sa_inspect(users[0]).attrs.profile.loaded_value is not NO_VALUE

    statements.clear()
    users = run(QuerySet(Phase4User, adapter).prefetch_related("posts"))
    assert len(users) == 2
    assert len(statements) == 2
    assert sorted(len(user.posts) for user in users) == [1, 2]
    assert all(
        sa_inspect(user).attrs.posts.loaded_value is not NO_VALUE for user in users
    )


def test_nested_prefetch_loads_each_relationship_level_in_batches(relationship_db):
    _engine, adapter, statements = relationship_db

    users = run(QuerySet(Phase4User, adapter).prefetch_related("posts__comments"))

    assert len(users) == 2
    assert len(statements) == 3
    ada = next(user for user in users if user.name == "Ada")
    assert [len(post.comments) for post in ada.posts] == [2, 1]
    assert all(
        sa_inspect(post).attrs.comments.loaded_value is not NO_VALUE
        for post in ada.posts
    )


def test_mixed_loading_strategies_with_shared_prefix_are_normalized(relationship_db):
    _engine, adapter, statements = relationship_db

    users = run(
        QuerySet(Phase4User, adapter)
        .select_related("profile")
        .prefetch_related("profile__user")
    )

    assert len(users) == 2
    # The explicit prefix stays joined; the nested tail is select-in loaded.
    assert len(statements) == 2
    assert users[0].profile.bio
    assert users[0].profile.user.name == users[0].name


def test_independent_joined_and_selectin_paths_can_be_combined(relationship_db):
    _engine, adapter, statements = relationship_db

    users = run(
        QuerySet(Phase4User, adapter)
        .select_related("profile")
        .prefetch_related("posts")
    )

    assert len(users) == 2
    assert len(statements) == 2
    assert users[0].profile.bio
    assert len(users[0].posts) == 2


def test_filter_join_does_not_suppress_relationship_eager_loading(relationship_db):
    _engine, adapter, statements = relationship_db

    posts = run(
        QuerySet(Phase4Post, adapter)
        .filter(author__name="Ada")
        .select_related("author")
    )

    assert len(posts) == 2
    assert len(statements) == 1
    assert all(post.author.name == "Ada" for post in posts)
    assert all(
        sa_inspect(post).attrs.author.loaded_value is not NO_VALUE for post in posts
    )


def test_eager_loading_options_are_ignored_for_projection_queries(relationship_db):
    _engine, adapter, statements = relationship_db

    rows = run(QuerySet(Phase4User, adapter).select_related("profile").values("name"))

    assert rows == [{"name": "Ada"}, {"name": "Grace"}]
    assert len(statements) == 1


def test_cartesian_detection_demotes_sibling_collection_joinedloads():
    graph = JoinGraph(Phase4User)
    planner_pass = CartesianDetectionPass()
    directives = [
        LoadDirective("posts", LoadStrategy.JOINEDLOAD, "explicit"),
        LoadDirective("posts__comments", LoadStrategy.JOINEDLOAD, "explicit"),
    ]

    result = planner_pass.execute(graph, QueryContext(), directives)

    # These are a parent collection and a nested collection, not siblings.
    assert [item.strategy for item in result.directives] == [
        LoadStrategy.JOINEDLOAD,
        LoadStrategy.JOINEDLOAD,
    ]


def test_cartesian_detection_demotes_second_sibling_collection():
    graph = JoinGraph(Phase4Post)
    directives = [
        LoadDirective("comments", LoadStrategy.JOINEDLOAD, "explicit"),
        LoadDirective("tags", LoadStrategy.JOINEDLOAD, "explicit"),
    ]
    result = CartesianDetectionPass().execute(graph, QueryContext(), directives)
    assert [item.strategy for item in result.directives] == [
        LoadStrategy.JOINEDLOAD,
        LoadStrategy.SELECTINLOAD,
    ]


def test_path_strategy_conflicts_demote_overlapping_joined_chains():
    graph = JoinGraph(Phase4User)
    directives = [
        LoadDirective("profile", LoadStrategy.JOINEDLOAD, "select"),
        LoadDirective("profile__user", LoadStrategy.SELECTINLOAD, "prefetch"),
    ]

    result = PathStrategyConflictPass().execute(graph, QueryContext(), directives)

    assert result.directives[0].segment_strategies == ()
    assert result.directives[1].segment_strategies == (
        LoadStrategy.JOINEDLOAD,
        LoadStrategy.SELECTINLOAD,
    )


def test_path_strategy_conflicts_do_not_demote_independent_paths():
    graph = JoinGraph(Phase4User)
    directives = [
        LoadDirective("profile", LoadStrategy.JOINEDLOAD, "select"),
        LoadDirective("posts", LoadStrategy.SELECTINLOAD, "prefetch"),
    ]

    result = PathStrategyConflictPass().execute(graph, QueryContext(), directives)

    assert result.directives == []


def test_redundant_path_pass_never_skips_filter_join_relationship_loading():
    graph = JoinGraph(Phase4User)
    graph.add_node("profile")
    directives = [
        LoadDirective("profile", LoadStrategy.JOINEDLOAD, "explicit"),
    ]

    result = RedundantPathPass().execute(graph, QueryContext(), directives)

    assert result.directives[0].strategy == LoadStrategy.JOINEDLOAD


def test_redundant_path_pass_skips_only_a_prefix_covered_by_longer_path():
    graph = JoinGraph(Phase4User)
    directives = [
        LoadDirective("posts", LoadStrategy.SELECTINLOAD, "explicit"),
        LoadDirective("posts__comments", LoadStrategy.SELECTINLOAD, "explicit"),
    ]

    result = RedundantPathPass().execute(graph, QueryContext(), directives)

    assert result.directives[0].strategy == LoadStrategy.SKIP
    assert result.directives[1].strategy == LoadStrategy.SELECTINLOAD


def test_depth_splitting_validates_depth_and_splits_nested_path():
    with pytest.raises(ValueError, match="at least 1"):
        DepthSplittingPass(0)

    with pytest.raises(TypeError, match="integer"):
        DepthSplittingPass(True)

    result = DepthSplittingPass(2).execute(
        JoinGraph(Phase4User),
        QueryContext(),
        [LoadDirective("posts__comments__post", LoadStrategy.JOINEDLOAD, "explicit")],
    )

    assert [(item.path, item.strategy) for item in result.directives] == [
        ("posts__comments", LoadStrategy.JOINEDLOAD),
        ("posts__comments__post", LoadStrategy.SELECTINLOAD),
    ]


def test_query_context_is_immutable_and_freezes_path_collections():
    context = QueryContext(select_related_paths={"profile"})

    assert context.select_related_paths == frozenset({"profile"})
    with pytest.raises((AttributeError, TypeError)):
        context.limit = 10


def test_default_planner_is_deterministic_and_preserves_requested_paths():
    graph = JoinGraph(Phase4User)
    context = QueryContext(
        select_related_paths=frozenset({"profile"}),
        prefetch_paths=frozenset({"posts"}),
    )

    planner = create_default_planner(graph, max_joinedload_depth=2)
    first = planner.plan(graph, context)
    second = planner.plan(graph, context)

    assert [(item.path, item.strategy) for item in first] == [
        ("profile", LoadStrategy.JOINEDLOAD),
        ("posts", LoadStrategy.SELECTINLOAD),
    ]
    assert first == second


def test_many_to_many_prefetch_executes_in_a_separate_batch(relationship_db):
    _engine, adapter, statements = relationship_db

    posts = run(QuerySet(Phase4Post, adapter).prefetch_related("tags"))

    tagged = next(post for post in posts if post.title == "Analytical Engine")
    assert len(statements) == 2
    assert [tag.name for tag in tagged.tags] == ["history"]
    assert sa_inspect(tagged).attrs.tags.loaded_value is not NO_VALUE


def test_nested_select_related_uses_one_joined_query(relationship_db):
    _engine, adapter, statements = relationship_db

    posts = run(QuerySet(Phase4Post, adapter).select_related("author__profile"))

    assert len(statements) == 1
    ada_post = next(post for post in posts if post.title == "Analytical Engine")
    assert ada_post.author.profile.bio == "Mathematician"
    assert sa_inspect(ada_post.author).attrs.profile.loaded_value is not NO_VALUE


def test_nested_many_to_many_prefetch_loads_in_batched_levels(relationship_db):
    _engine, adapter, statements = relationship_db

    users = run(QuerySet(Phase4User, adapter).prefetch_related("posts__tags"))

    assert len(statements) == 3
    ada = next(user for user in users if user.name == "Ada")
    tagged_post = next(post for post in ada.posts if post.title == "Analytical Engine")
    assert [tag.name for tag in tagged_post.tags] == ["history"]


def test_fetch_all_and_get_handle_mapping_level_joined_collection_loading(
    relationship_db,
):
    _engine, adapter, statements = relationship_db

    parents = run(QuerySet(Phase4JoinedParent, adapter))
    assert len(parents) == 1
    assert [child.name for child in parents[0].children] == ["child-a", "child-b"]
    assert len(statements) == 1

    statements.clear()
    count = asyncio.run(QuerySet(Phase4JoinedParent, adapter).count())
    assert count == 1

    statements.clear()
    parent = asyncio.run(
        QuerySet(Phase4JoinedParent, adapter).filter(name="joined-parent").get()
    )
    assert [child.name for child in parent.children] == ["child-a", "child-b"]
    assert len(statements) == 1


def test_join_promotion_promotes_complete_filter_path():
    graph = JoinGraph(Phase4Post)
    graph.add_node("author__profile", used_in_filter=True)

    result = JoinPromotionPass(graph).execute(graph, QueryContext(), [])

    assert graph.get_node("author").join_type == JoinType.INNER
    assert graph.get_node("author__profile").join_type == JoinType.INNER
    assert len(result.mutations) == 2


@pytest.mark.parametrize(
    "flags",
    [
        {"used_in_or_context": True},
        {"used_in_negation": True},
        {"has_nullable_check": True},
    ],
)
def test_join_promotion_preserves_left_joins_for_nullable_boolean_contexts(flags):
    graph = JoinGraph(Phase4Post)
    graph.add_node("author__profile", used_in_filter=True, **flags)

    JoinPromotionPass(graph).execute(graph, QueryContext(), [])

    assert graph.get_node("author").join_type == JoinType.LEFT
    assert graph.get_node("author__profile").join_type == JoinType.LEFT


def test_nested_sibling_loader_paths_get_mixed_strategies_without_conflict():
    graph = JoinGraph(Phase4User)
    directives = [
        LoadDirective("posts__comments", LoadStrategy.JOINEDLOAD, "joined"),
        LoadDirective("posts__tags", LoadStrategy.SELECTINLOAD, "select-in"),
    ]

    result = PathStrategyConflictPass().execute(graph, QueryContext(), directives)

    assert result.directives[0].segment_strategies == (
        LoadStrategy.SELECTINLOAD,
        LoadStrategy.JOINEDLOAD,
    )
    assert result.directives[1].segment_strategies == ()


def test_join_graph_traversals_terminate_if_internal_tree_is_corrupted():
    graph = JoinGraph(Phase4User)
    node = graph.add_node("posts")
    node.children.append(graph.root)

    assert [item.path for item in graph.walk_bfs()] == ["posts"]
    assert [item.path for item in graph.walk_dfs()] == ["posts"]
    assert [item.path for item in graph.subtree("posts")] == ["posts"]
    assert graph.validate()


def test_depth_splitting_executes_a_mixed_joined_selectin_chain(relationship_db):
    _engine, adapter, statements = relationship_db

    users = run(
        QuerySet(
            Phase4User,
            adapter,
            config=ORMConfig(max_joinedload_depth=1),
        ).select_related("profile__user")
    )

    assert len(users) == 2
    assert len(statements) == 2
    assert users[0].profile.user.name == users[0].name
    assert sa_inspect(users[0]).attrs.profile.loaded_value is not NO_VALUE
    assert sa_inspect(users[0].profile).attrs.user.loaded_value is not NO_VALUE


def test_count_uses_distinct_root_identity_for_collection_filter_joins(relationship_db):
    _engine, adapter, _statements = relationship_db

    count = asyncio.run(
        QuerySet(Phase4User, adapter).filter(posts__title__icontains="").count()
    )

    assert count == 2


def test_load_directive_freezes_segment_strategy_iterables():
    strategies = [LoadStrategy.JOINEDLOAD, LoadStrategy.SELECTINLOAD]
    directive = LoadDirective(
        "profile__user",
        LoadStrategy.SELECTINLOAD,
        "mixed chain",
        segment_strategies=strategies,
    )
    strategies.append(LoadStrategy.JOINEDLOAD)

    assert directive.segment_strategies == (
        LoadStrategy.JOINEDLOAD,
        LoadStrategy.SELECTINLOAD,
    )
