from __future__ import annotations

from ..directives import LoadDirective, LoadStrategy, QueryContext
from ..protocols import JoinGraphReadable
from .base import PassResult, PlanningPass


class PathStrategyConflictPass(PlanningPass):
    """Resolve strategy conflicts at shared prefixes of eager-loader chains.

    A directive's strategy is the default for its complete path. SQLAlchemy
    supports mixed strategies within one chain (for example,
    ``joinedload(User.profile).selectinload(Profile.posts)``), so overlapping
    directives should not be flattened to one strategy.

    This pass resolves the strategy for each path segment:
    - An explicit directive for the exact prefix wins.
    - Otherwise, the nearest explicit descendant determines the prefix.
    - If equally-near descendants disagree, select-in loading is chosen for
      that shared prefix because it avoids collection row multiplication.

    The leaf strategy remains the directive's requested strategy.
    """

    name = "path_strategy_conflicts"

    def execute(
        self,
        graph: JoinGraphReadable,
        context: QueryContext,
        current_directives: list[LoadDirective],
    ) -> PassResult:
        result = PassResult(pass_name=self.name)
        active = [
            directive
            for directive in current_directives
            if directive.strategy
            in {
                LoadStrategy.JOINEDLOAD,
                LoadStrategy.SELECTINLOAD,
            }
        ]
        explicit = {directive.path: directive for directive in active}
        resolved: list[LoadDirective] = []

        for directive in current_directives:
            if directive.strategy not in {
                LoadStrategy.JOINEDLOAD,
                LoadStrategy.SELECTINLOAD,
            }:
                resolved.append(directive)
                continue

            parts = directive.path.split("__")
            strategies: list[LoadStrategy] = []
            for depth in range(1, len(parts) + 1):
                prefix = "__".join(parts[:depth])
                exact = explicit.get(prefix)
                if exact is not None:
                    strategies.append(exact.strategy)
                    continue

                descendants = [
                    candidate
                    for candidate in active
                    if candidate.path.startswith(prefix + "__")
                ]
                nearest_depth = min(
                    len(candidate.path.split("__")) for candidate in descendants
                )
                nearest_strategies = {
                    candidate.strategy
                    for candidate in descendants
                    if len(candidate.path.split("__")) == nearest_depth
                }
                strategies.append(
                    next(iter(nearest_strategies))
                    if len(nearest_strategies) == 1
                    else LoadStrategy.SELECTINLOAD
                )

            segment_strategies = tuple(strategies)
            default_chain = tuple(directive.strategy for _ in parts)
            if segment_strategies == default_chain:
                resolved.append(directive)
            else:
                resolved.append(
                    LoadDirective(
                        path=directive.path,
                        strategy=directive.strategy,
                        reason=directive.reason,
                        original_strategy=directive.original_strategy,
                        segment_strategies=segment_strategies,
                    )
                )
                result.warnings.append(
                    f"Resolved mixed eager-loading strategies for {directive.path!r}: "
                    + " -> ".join(strategy.name for strategy in segment_strategies)
                )

        if result.warnings:
            result.directives = resolved
        return result
