from __future__ import annotations

from collections.abc import Sequence

from async_orm_kit._logging import get_logger
from async_orm_kit.exceptions import RelationshipError

from .directives import LoadDirective, LoadStrategy, QueryContext
from .passes.base import PassResult, PlanningPass
from .protocols import JoinGraphReadable

logger = get_logger(__name__)


class QueryPlanner:
    """
    Orchestrates planning passes in a defined order.

    The planner itself holds NO planning logic — it only:
    1. Manages the pass pipeline
    2. Threads state between passes
    3. Collects audit information

    This is the Strategy + Chain of Responsibility pattern:
    passes can be added, removed, or reordered without
    touching any other code.
    """

    def __init__(self, passes: Sequence[PlanningPass] | None = None):
        self._passes: list[PlanningPass] = list(passes or [])
        self._last_results: list[PassResult] = []

    def add_pass(self, planning_pass: PlanningPass) -> QueryPlanner:
        """Fluent API for building the pipeline."""
        self._passes.append(planning_pass)
        return self

    def plan(
        self,
        graph: JoinGraphReadable,
        context: QueryContext,
    ) -> list[LoadDirective]:
        """
        Run all passes in sequence.

        Each pass receives the directives produced by the previous pass.
        The first pass receives the initial directives built from
        the QueryContext's raw paths.
        """
        self._last_results = []

        # Build initial directives from raw user intent
        directives = self._build_initial_directives(graph, context)

        # Run each pass
        for planning_pass in self._passes:
            result = planning_pass.execute(graph, context, directives)
            self._last_results.append(result)

            if result.directives:
                directives = result.directives

            if result.warnings:
                for w in result.warnings:
                    logger.warning(
                        "planning_warning",
                        pass_name=planning_pass.name,
                        detail=w,
                    )

            if result.mutations:
                for m in result.mutations:
                    logger.info(
                        "planning_mutation",
                        pass_name=planning_pass.name,
                        detail=m,
                    )

        return directives

    def _build_initial_directives(
        self,
        graph: JoinGraphReadable,
        context: QueryContext,
    ) -> list[LoadDirective]:
        """
        Translate raw user paths into initial LoadDirectives.

        This is pure translation — no intelligence yet.
        The passes add the intelligence.
        """
        directives: list[LoadDirective] = []

        for path in sorted(context.select_related_paths):
            relationships = graph.resolve_relationship_path(path)
            if relationships is None:
                raise RelationshipError(f"Invalid select_related path {path!r}.")
            if any(relationship.uselist for relationship in relationships):
                raise RelationshipError(
                    f"select_related path {path!r} crosses a collection relationship; "
                    "use prefetch_related() instead."
                )
            directives.append(
                LoadDirective(
                    path=path,
                    strategy=LoadStrategy.JOINEDLOAD,
                    reason="User-requested select_related",
                )
            )

        for path in sorted(context.prefetch_paths):
            if path in context.select_related_paths:
                continue  # select_related takes precedence for the exact path

            relationships = graph.resolve_relationship_path(path)
            if relationships is None:
                raise RelationshipError(f"Invalid prefetch_related path {path!r}.")
            if relationships[-1].uselist:
                reason = "User-requested prefetch (collection)"
            else:
                reason = "User-requested prefetch (scalar relationship)"
            strategy = LoadStrategy.SELECTINLOAD

            directives.append(
                LoadDirective(
                    path=path,
                    strategy=strategy,
                    reason=reason,
                )
            )

        return directives

    def explain(self) -> str:
        """
        Human-readable explanation of what each pass did.
        Invaluable for debugging slow queries.
        """
        lines = ["Query Plan Explanation", "=" * 40]

        for result in self._last_results:
            lines.append(f"\n── {result.pass_name} ──")

            if not result.had_effect:
                lines.append("  (no effect)")
                continue

            for m in result.mutations:
                lines.append(f"  [MUTATE] {m}")
            for w in result.warnings:
                lines.append(f"  [WARN]   {w}")

        lines.append("\n── Final Directives ──")
        if self._last_results and self._last_results[-1].directives:
            for d in self._last_results[-1].directives:
                marker = " ⚠ DEMOTED" if d.was_demoted else ""
                lines.append(f"  {d.path:40s} → {d.strategy.name:15s}{marker}")
                lines.append(f"    reason: {d.reason}")

        return "\n".join(lines)
