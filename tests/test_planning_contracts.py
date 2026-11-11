"""Relationship graph integrity, diagnostics, and extensible planning contracts."""

from __future__ import annotations

import pytest
from test_relationship_planning import Phase4Post, Phase4User

from async_orm_kit.exceptions import RelationshipError
from async_orm_kit.query.planning.directives import (
    LoadDirective,
    LoadStrategy,
    QueryContext,
)
from async_orm_kit.query.planning.passes.base import PassResult, PlanningPass
from async_orm_kit.query.planning.passes.depth_splitting import DepthSplittingPass
from async_orm_kit.query.planning.passes.join_promotion import JoinPromotionPass
from async_orm_kit.query.planning.passes.path_conflicts import PathStrategyConflictPass
from async_orm_kit.query.planning.passes.redundant_path import RedundantPathPass
from async_orm_kit.query.planning.passes.strategy_selection import AggregateStrategyPass
from async_orm_kit.query.planning.planner import QueryPlanner
from async_orm_kit.query.relations.enums import JoinType
from async_orm_kit.query.relations.graph import JoinGraph
from async_orm_kit.query.relations.node import JoinNode


def directive(path="profile", strategy=LoadStrategy.JOINEDLOAD, **kwargs):
    return LoadDirective(path, strategy, "requested", **kwargs)


@pytest.mark.parametrize(
    "kwargs,error",
    [
        ({"path": ""}, ValueError),
        ({"path": "bad____path"}, ValueError),
        ({"path": " bad"}, ValueError),
        ({"strategy": "joined"}, TypeError),
        ({"reason": ""}, ValueError),
        ({"original_strategy": "joined"}, TypeError),
        ({"segment_strategies": None}, TypeError),
        (
            {"segment_strategies": (LoadStrategy.JOINEDLOAD, LoadStrategy.SKIP)},
            ValueError,
        ),
        ({"segment_strategies": ("joined",)}, TypeError),
    ],
)
def test_load_directive_validates_configuration(kwargs, error):
    values = {
        "path": "profile",
        "strategy": LoadStrategy.JOINEDLOAD,
        "reason": "requested",
    }
    values.update(kwargs)
    with pytest.raises(error):
        LoadDirective(**values)


@pytest.mark.parametrize(
    "kwargs,error",
    [
        ({"has_aggregates": 1}, TypeError),
        ({"is_distinct": None}, TypeError),
        ({"limit": -1}, ValueError),
        ({"limit": True}, ValueError),
        ({"select_related_paths": [""]}, ValueError),
        ({"prefetch_paths": [1]}, ValueError),
    ],
)
def test_query_context_rejects_ambiguous_values(kwargs, error):
    with pytest.raises(error):
        QueryContext(**kwargs)


def test_node_properties_flags_clone_and_ancestry():
    root = JoinNode("", Phase4User, join_type=JoinType.INNER)
    parent = JoinNode("posts", Phase4Post)
    child = JoinNode(
        "posts__comments",
        object(),
        used_in_filter=True,
        used_in_or_context=True,
        has_nullable_check=True,
        used_in_negation=True,
        used_in_order_by=True,
        used_in_annotation=True,
    )
    root.add_child(parent)
    parent.add_child(child)
    assert root.is_root and root.depth == 0 and root.segment == ""
    assert parent.depth == 1 and child.depth == 2 and child.depth == 2
    assert child.segment == "comments" and child.parent_path == "posts"
    assert child.ancestors() == [parent]
    assert child.is_left and not child.is_inner and root.is_inner
    assert hash(child) == hash(JoinNode(child.path, None))
    assert child == JoinNode(child.path, None) and child != object()
    assert "[F,OR,NULL,NEG,ORD,ANN]" in repr(child)
    assert repr(root) == "JoinNode('', INNER)"
    clone = child._clone()
    assert clone == child and clone.parent is None and clone.children == []
    assert str(JoinType.LEFT) == "LEFT"


def test_node_attachment_rejects_conflicting_ownership_and_cycles():
    root = JoinNode("", None)
    child = JoinNode("posts", None)
    for value, error in [
        (None, TypeError),
        (root, ValueError),
        (JoinNode("wrong__depth", None), ValueError),
    ]:
        with pytest.raises(error):
            root.add_child(value)
    root.add_child(child)
    with pytest.raises(ValueError, match="Duplicate"):
        root.add_child(child)
    other = JoinNode("", None)
    with pytest.raises(ValueError, match="another parent"):
        other.add_child(child)
    with pytest.raises(ValueError, match="Duplicate"):
        root.add_child(JoinNode("posts", None))
    # Dataclass construction allows a parent reference; add_child must still
    # detect a malformed ancestor chain rather than introducing a cycle.
    ancestor = JoinNode("posts", None)
    parent = JoinNode("", None, parent=ancestor)
    with pytest.raises(ValueError, match="cycle"):
        parent.add_child(ancestor)


def test_graph_traversal_mutation_and_diagnostics():
    graph = JoinGraph(Phase4User)
    assert graph.max_depth() == 0 and graph.subtree("absent") == []
    post = graph.add_node("posts", used_in_filter=True)
    comment = graph.add_node("posts__comments", used_in_annotation=True)
    profile = graph.add_node("profile")
    assert list(graph) == [post, comment, profile] and len(graph) == 3
    assert "posts" in graph and graph.has_node("posts")
    assert graph.filter_nodes() == [post] and graph.left_joins() == [
        post,
        comment,
        profile,
    ]
    assert graph.inner_joins() == [] and graph.max_depth() == 2
    assert graph.leaf_nodes() == [comment, profile]
    assert [n.path for n in graph.walk_bfs()] == ["posts", "profile", "posts__comments"]
    assert [n.path for n in graph.walk_dfs()] == ["posts", "posts__comments", "profile"]
    assert graph.subtree("posts") == [post, comment]
    assert "comments" in graph.to_ascii_tree() and "joins=3" in repr(graph)
    assert graph.validate() == []
    assert graph._resolve_relationship_from_model("posts") is Phase4User.posts.property
    with pytest.raises(ValueError):
        graph.remove_node("posts")
    assert not graph.remove_node("missing")
    graph.set_join_type("posts", "INNER")
    assert graph.inner_joins() == [post]
    with pytest.raises(KeyError):
        graph.set_join_type("missing", "INNER")
    with pytest.raises(ValueError):
        graph.set_join_type("posts", "invalid")
    graph.add_node(
        "posts",
        join_type=JoinType.INNER,
        used_in_or_context=True,
        has_nullable_check=True,
        used_in_negation=True,
        used_in_order_by=True,
        used_in_annotation=True,
    )
    assert "negation" in graph._flag_summary(
        post
    ) and "null_check" in graph._flag_summary(post)
    assert graph.remove_node("posts__comments") and graph.remove_node("posts")
    assert graph.validate() == []


@pytest.mark.parametrize(
    "path,error",
    [
        (None, TypeError),
        ("", ValueError),
        ("__posts", ValueError),
        ("posts__", ValueError),
        ("posts____comments", ValueError),
        (" posts", ValueError),
    ],
)
def test_graph_rejects_malformed_paths(path, error):
    graph = JoinGraph(Phase4User)
    with pytest.raises(error):
        graph.add_node(path)
    assert graph.resolve_relationship_path(path) is None


def test_graph_rejects_wrong_relationship_metadata():
    with pytest.raises(ValueError, match="does not match"):
        JoinGraph(Phase4User).add_node(
            "posts", relationship=Phase4User.profile.property
        )
    with pytest.raises(ValueError, match="does not match"):
        JoinGraph(Phase4Post).add_node("author", relationship=Phase4User.posts.property)


@pytest.mark.parametrize(
    "damage,expected",
    [
        ("parent", "no parent reference"),
        ("detach", "missing from"),
        ("relationship", "no relationship"),
        ("wrong_parent", "attached to"),
        ("renamed", "does not match"),
        ("unreachable", "Unreachable"),
        ("order", "before its parent"),
        ("duplicate", "duplicate paths"),
        ("cycle", "Cycle"),
        ("mismatch", "different nodes"),
    ],
)
def test_graph_validation_reports_corrupted_structure(damage, expected):
    graph = JoinGraph(Phase4User)
    parent = graph.add_node("posts")
    child = graph.add_node("posts__comments")
    if damage == "parent":
        child.parent = None
    elif damage == "detach":
        parent.children = []
    elif damage == "relationship":
        child.relationship = None
    elif damage == "wrong_parent":
        child.parent = graph.root
    elif damage == "renamed":
        child.path = "comments"
    elif damage == "unreachable":
        graph.root.children = []
    elif damage == "order":
        graph._join_order.reverse()
    elif damage == "duplicate":
        graph._join_order.append(parent)
    elif damage == "cycle":
        parent.children.append(parent)
    elif damage == "mismatch":
        graph._join_order = []
    assert any(expected in message for message in graph.validate())
    if damage == "cycle":
        assert list(graph.walk_bfs()) == [parent, child]
        assert list(graph.walk_dfs()) == [parent, child]
        assert graph.subtree("posts") == [parent, child]


def test_planner_extensions_and_explanations():
    class ReportingPass(PlanningPass):
        name = "reporting"

        def execute(self, graph, context, current_directives):
            return PassResult(
                self.name,
                [
                    directive(
                        strategy=LoadStrategy.SELECTINLOAD,
                        original_strategy=LoadStrategy.JOINEDLOAD,
                    )
                ],
                ["diagnostic"],
                ["mutation"],
            )

    class NoEffect(PlanningPass):
        name = "no_effect"

        def execute(self, *args):
            return PassResult(self.name)

    graph = JoinGraph(Phase4User)
    planner = QueryPlanner().add_pass(NoEffect()).add_pass(ReportingPass())
    assert not PassResult("empty").had_effect
    assert PassResult("warning", warnings=["w"]).had_effect
    assert PassResult("mutation", mutations=["m"]).had_effect
    results = planner.plan(
        graph,
        QueryContext(select_related_paths=["profile"], prefetch_paths=["profile"]),
    )
    assert results[0].was_demoted
    assert "demoted" in repr(results[0]) and "demoted" not in repr(directive())
    report = planner.explain()
    for word in ["no effect", "MUTATE", "WARN", "DEMOTED", "reason"]:
        assert word in report
    assert "Final Directives" in QueryPlanner().explain()
    with pytest.raises(RelationshipError):
        QueryPlanner().plan(graph, QueryContext(select_related_paths=["absent"]))
    with pytest.raises(RelationshipError):
        QueryPlanner().plan(graph, QueryContext(select_related_paths=["posts"]))
    with pytest.raises(RelationshipError):
        QueryPlanner().plan(graph, QueryContext(prefetch_paths=["absent"]))
    assert (
        QueryPlanner().plan(graph, QueryContext(prefetch_paths=["profile"]))[0].strategy
        == LoadStrategy.SELECTINLOAD
    )


def test_aggregate_skipped_and_depth_planning_strategies():
    graph = JoinGraph(Phase4User)
    context = QueryContext(has_aggregates=True)
    skipped = directive(strategy=LoadStrategy.SKIP)
    initial = [directive(), skipped]
    result = AggregateStrategyPass().execute(graph, context, initial)
    assert result.directives[0].strategy == LoadStrategy.SELECTINLOAD
    assert result.directives[1] is skipped
    assert RedundantPathPass().execute(graph, context, [skipped]).directives == [
        skipped
    ]
    assert not PathStrategyConflictPass().execute(graph, context, [skipped]).had_effect
    deep = [
        directive("posts__comments__post"),
        directive("posts__comments__post__author"),
    ]
    result = DepthSplittingPass(1).execute(graph, context, deep)
    assert len({d.path for d in result.directives}) == len(result.directives)


def test_join_promotion_requires_every_ancestor_to_support_inner():
    graph = JoinGraph(Phase4User)
    child = graph.add_node("posts__comments", used_in_filter=True)
    parent = child.parent
    planning = JoinPromotionPass(graph)
    child.join_type = JoinType.INNER
    assert not planning._should_promote(child)
    child.join_type = JoinType.LEFT
    parent.used_in_filter = False
    assert not planning._ancestors_support_inner(child)
    assert not planning.execute(graph, QueryContext(), []).mutations
    parent.used_in_filter = True
    assert planning._ancestors_support_inner(child)
    parent.join_type = JoinType.INNER
    assert planning._ancestors_support_inner(child)
