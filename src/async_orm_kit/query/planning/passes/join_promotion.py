from __future__ import annotations

from ...relations.enums import JoinType
from ..directives import LoadDirective, QueryContext
from ..protocols import JoinGraphMutable, JoinGraphReadable, JoinNodeReadable
from .base import PassResult, PlanningPass


class JoinPromotionPass(PlanningPass):
    """
    Promote LEFT → INNER for joins mandated by WHERE clauses.

    This is the ONLY pass that mutates the JoinGraph, and it
    does so through the narrow JoinGraphMutable protocol.
    """

    name = "join_promotion"

    def __init__(self, graph_mut: JoinGraphMutable):
        # Separate mutable reference — explicit about what this pass can do
        self._graph_mut = graph_mut

    def execute(
        self,
        graph: JoinGraphReadable,
        context: QueryContext,
        current_directives: list[LoadDirective],
    ) -> PassResult:
        result = PassResult(pass_name=self.name)

        for node in graph.join_order:
            if not self._should_promote(node):
                continue

            if not self._ancestors_support_inner(node):
                continue

            self._graph_mut.set_join_type(node.path, JoinType.INNER)
            result.mutations.append(
                f"Promoted {node.path}: LEFT → INNER (filter-mandated)"
            )

        return result

    def _should_promote(self, node: JoinNodeReadable) -> bool:
        """All conditions must be met for safe promotion."""
        if node.join_type != JoinType.LEFT:
            return False
        if not node.used_in_filter:
            return False
        if node.used_in_negation:
            return False
        if node.used_in_or_context:
            return False
        if node.has_nullable_check:
            return False
        return True

    def _ancestors_support_inner(self, node: JoinNodeReadable) -> bool:
        """
        Only promote if all ancestors are INNER or will be promoted.

        We can't have: A (LEFT) → B (INNER)
        If A is NULL, B can't match, eliminating the row despite LEFT JOIN.
        """
        current = node.parent
        while current is not None and current.path != "":
            # Ancestor is already INNER — OK
            if current.join_type == JoinType.INNER:
                current = current.parent
                continue

            # Ancestor is LEFT but will be promoted — OK
            if self._should_promote(current):
                current = current.parent
                continue

            # Ancestor is LEFT and won't be promoted — BLOCK
            return False

        return True
