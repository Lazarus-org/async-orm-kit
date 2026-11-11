from __future__ import annotations

from .passes import (
    AggregateStrategyPass,
    CartesianDetectionPass,
    DepthSplittingPass,
    JoinPromotionPass,
    PathStrategyConflictPass,
    RedundantPathPass,
)
from .planner import QueryPlanner
from .protocols import JoinGraphMutable


def create_default_planner(
    graph_mut: JoinGraphMutable,
    max_joinedload_depth: int = 2,
) -> QueryPlanner:
    """
    Build the standard planning pipeline.

    Pass order matters:
    1. Join promotion   — mutates graph, must run first
    2. Aggregate check  — may demote joinedload → selectinload
    3. Depth splitting  — splits deep joinedloads
    4. Cartesian detect — demotes sibling collections
    5. Path conflicts   — normalize overlapping loader strategies
    6. Redundant paths  — remove loader prefixes already covered by longer paths

    Each pass operates on the output of the previous one.
    """
    return (
        QueryPlanner()
        .add_pass(JoinPromotionPass(graph_mut))
        .add_pass(AggregateStrategyPass())
        .add_pass(DepthSplittingPass(max_joinedload_depth))
        .add_pass(CartesianDetectionPass())
        .add_pass(PathStrategyConflictPass())
        .add_pass(RedundantPathPass())
    )
