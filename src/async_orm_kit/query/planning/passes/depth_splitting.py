from __future__ import annotations

from ..directives import LoadDirective, LoadStrategy, QueryContext
from ..protocols import JoinGraphReadable
from .base import PassResult, PlanningPass


class DepthSplittingPass(PlanningPass):
    """
    Split deep joinedload paths into a joinedload head
    and selectinload tail.

    Prevents excessively wide SQL SELECTs from deep join chains.
    """

    name = "depth_splitting"

    def __init__(self, max_joinedload_depth: int = 2):
        if isinstance(max_joinedload_depth, bool) or not isinstance(
            max_joinedload_depth, int
        ):
            raise TypeError("max_joinedload_depth must be an integer.")
        if max_joinedload_depth < 1:
            raise ValueError("max_joinedload_depth must be at least 1.")
        self._max_depth = max_joinedload_depth

    def execute(
        self,
        graph: JoinGraphReadable,
        context: QueryContext,
        current_directives: list[LoadDirective],
    ) -> PassResult:
        result = PassResult(pass_name=self.name)
        new_directives: list[LoadDirective] = []
        seen_paths = set()

        for directive in current_directives:
            if directive.strategy != LoadStrategy.JOINEDLOAD:
                new_directives.append(directive)
                continue

            depth = directive.path.count("__") + 1

            if depth <= self._max_depth:
                new_directives.append(directive)
                continue

            parts = directive.path.split("__")

            # Head
            head_path = "__".join(parts[: self._max_depth])
            if head_path not in seen_paths:
                new_directives.append(
                    LoadDirective(
                        path=head_path,
                        strategy=LoadStrategy.JOINEDLOAD,
                        reason=f"Head of deep path (depth ≤ {self._max_depth})",
                        original_strategy=directive.strategy,
                    )
                )
                seen_paths.add(head_path)

            # Tail segments
            for i in range(self._max_depth, len(parts)):
                tail_path = "__".join(parts[: i + 1])
                if tail_path not in seen_paths:
                    new_directives.append(
                        LoadDirective(
                            path=tail_path,
                            strategy=LoadStrategy.SELECTINLOAD,
                            reason=f"Tail segment (depth {i + 1} > {self._max_depth})",
                            original_strategy=LoadStrategy.JOINEDLOAD,
                        )
                    )
                    seen_paths.add(tail_path)

        result.directives = new_directives
        return result
