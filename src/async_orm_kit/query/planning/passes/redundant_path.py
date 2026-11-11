from __future__ import annotations

from ..directives import LoadDirective, LoadStrategy, QueryContext
from ..protocols import JoinGraphReadable
from .base import PassResult, PlanningPass


class RedundantPathPass(PlanningPass):
    """
    Remove loading paths that are strict prefixes of longer
    loader paths — the longer chained loader already traverses them.

    A JOIN used by WHERE/ORDER BY does not populate an ORM relationship.
    Therefore graph membership alone must never suppress eager loading.
    """

    name = "redundant_path"

    def execute(
        self,
        graph: JoinGraphReadable,
        context: QueryContext,
        current_directives: list[LoadDirective],
    ) -> PassResult:
        result = PassResult(pass_name=self.name)
        new_directives: list[LoadDirective] = []

        # Collect all active (non-skip) paths for prefix detection
        active_paths = {
            d.path for d in current_directives if d.strategy != LoadStrategy.SKIP
        }

        for directive in current_directives:
            if directive.strategy == LoadStrategy.SKIP:
                new_directives.append(directive)
                continue

            # A filter/order JOIN is not a relationship loader. Do not skip
            # paths merely because the JoinGraph contains the same path.

            # A strict prefix is loaded by the longer chained loader.
            if self._is_covered_prefix(directive.path, active_paths):
                new_directives.append(
                    LoadDirective(
                        path=directive.path,
                        strategy=LoadStrategy.SKIP,
                        reason="Prefix of a longer eager-loading path",
                        original_strategy=directive.strategy,
                    )
                )
                continue

            new_directives.append(directive)

        result.directives = new_directives
        return result

    @staticmethod
    def _is_covered_prefix(path: str, all_paths: set[str]) -> bool:
        """
        'a__b' is covered if 'a__b__c' exists in all_paths.

        But 'a__b' is NOT covered by 'a__b_extra' (must be
        a real path segment boundary).
        """
        prefix = path + "__"
        return any(p.startswith(prefix) for p in all_paths if p != path)
