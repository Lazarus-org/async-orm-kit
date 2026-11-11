from __future__ import annotations

from ..directives import LoadDirective, LoadStrategy, QueryContext
from ..protocols import JoinGraphReadable
from .base import PassResult, PlanningPass


class AggregateStrategyPass(PlanningPass):
    """
    When the query uses aggregates, demote joinedload to
    selectinload to prevent GROUP BY expansion.

    joinedload + aggregates forces every loaded column into
    GROUP BY, which:
    - Makes the query slower (more grouping columns)
    - Can produce incorrect counts (cartesian with joined tables)
    """

    name = "aggregate_strategy"

    def execute(
        self,
        graph: JoinGraphReadable,
        context: QueryContext,
        current_directives: list[LoadDirective],
    ) -> PassResult:
        result = PassResult(pass_name=self.name)

        # Only applies when aggregates are present on full model queries
        if not context.has_aggregates:
            return result

        new_directives: list[LoadDirective] = []

        for directive in current_directives:
            if directive.strategy == LoadStrategy.JOINEDLOAD:
                new_directives.append(
                    LoadDirective(
                        path=directive.path,
                        strategy=LoadStrategy.SELECTINLOAD,
                        reason="Aggregate context — preventing GROUP BY expansion",
                        original_strategy=directive.strategy,
                    )
                )
            else:
                new_directives.append(directive)

        result.directives = new_directives
        return result
