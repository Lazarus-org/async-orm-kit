from __future__ import annotations

from collections import defaultdict

from ..directives import LoadDirective, LoadStrategy, QueryContext
from ..protocols import JoinGraphReadable
from .base import PassResult, PlanningPass


class CartesianDetectionPass(PlanningPass):
    """
    Detect multiple collection joinedloads from the same parent
    and demote all but the first to selectinload.

    Why? If Parent has 10 Books and 5 Reviews, and both are
    joinedload, the result is 50 rows. Each additional collection
    multiplies the row count.
    """

    name = "cartesian_detection"

    def execute(
        self,
        graph: JoinGraphReadable,
        context: QueryContext,
        current_directives: list[LoadDirective],
    ) -> PassResult:
        result = PassResult(pass_name=self.name)

        # Group collection joinedloads by parent path
        parent_to_collections: dict[str, list[int]] = defaultdict(list)

        for idx, directive in enumerate(current_directives):
            if directive.strategy != LoadStrategy.JOINEDLOAD:
                continue

            rel = graph.resolve_relationship(directive.path)
            if rel is None or not rel.uselist:
                continue

            parent_path = self._parent_of(directive.path)
            parent_to_collections[parent_path].append(idx)

        # For each parent with multiple collection joins, demote extras
        new_directives = list(current_directives)

        for parent, indices in parent_to_collections.items():
            if len(indices) <= 1:
                continue

            result.warnings.append(
                f"Cartesian risk from parent '{parent}': "
                f"{len(indices)} collection joinedloads. "
                f"Demoting all but first to selectinload."
            )

            # Keep the first, demote the rest
            for idx in indices[1:]:
                old = new_directives[idx]
                new_directives[idx] = LoadDirective(
                    path=old.path,
                    strategy=LoadStrategy.SELECTINLOAD,
                    reason=f"Cartesian prevention — sibling collection "
                    f"already using joinedload from '{parent}'",
                    original_strategy=old.strategy,
                )

        result.directives = new_directives
        return result

    @staticmethod
    def _parent_of(path: str) -> str:
        parts = path.rsplit("__", 1)
        return parts[0] if len(parts) > 1 else "<root>"
