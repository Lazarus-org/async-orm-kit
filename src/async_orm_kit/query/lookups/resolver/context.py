from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass(frozen=True, slots=True)
class ResolverContext:
    """Context flags controlling field resolution and JOIN graph behavior.

    ResolverContext carries metadata about the query operation context through
    the entire field resolution pipeline. These flags determine how JOINs are
    registered, optimized, and executed. The context ensures consistent behavior
    across nested relationship traversals and complex query structures.

    Context flags influence JOIN type selection:
    - mark_filter=True: Marks JOINs as filter nodes, enabling INNER JOIN promotion
    - in_or_context=True: Prevents INNER JOIN optimization for OR block conditions
    - in_negation=True: Forces LEFT JOIN for NOT blocks to preserve NULL semantics
    - in_order_by=True: Marks JOINs used in ORDER BY for proper result ordering
    - in_annotation=True: Tracks JOINs used in annotations for SELECT clause
    - has_nullable_check=True: Indicates isnull lookup, affects JOIN type selection

    The context is immutable - modification methods return new instances with
    updated flags. This ensures thread safety and prevents accidental state
    mutation during query compilation.
    Attributes:
        mark_filter: If True, marks JOINs as filter nodes. Filter JOINs can be
                    promoted from LEFT to INNER JOIN when safe (no OR/NOT context).
        in_or_context: If True, prevents INNER JOIN promotion. OR conditions
                      require LEFT JOINs to preserve NULL rows for non-matching
                      branches.
        in_negation: If True, forces LEFT JOIN. NOT conditions need NULLs to
                    correctly identify non-matching rows.
        in_order_by: If True, marks JOINs used in ORDER BY clause. These JOINs
                    must be included even if not used elsewhere.
        in_annotation: If True, marks JOINs used in annotations. Tracks whichrelationships are referenced in SELECT clause expressions.
        has_nullable_check: If True, indicates an isnull lookup is present.
                Affects JOIN type selection for nullable checks.

    Example:
        >>> # Default context - no special behavior
        >>> context = ResolverContext()
        >>> # mark_filter=False, in_or_context=False, in_negation=False, ...
        >>>
        >>> # Filter context - enables INNER JOIN optimization
        >>> context = ResolverContext.for_filter()
        >>> # mark_filter=True, other flags False
        >>>
        >>> # OR context - prevents INNER JOIN promotion
        >>> context = ResolverContext(mark_filter=True).with_or()
        >>> # mark_filter=True, in_or_context=True
        >>>
        >>> # Negation context - forces LEFT JOIN
        >>> context = ResolverContext(mark_filter=True).with_negation()
        >>> # mark_filter=True, in_negation=True
        >>>
        >>> # Annotation context - tracks SELECT clause usage
        >>> context = ResolverContext.for_annotation()
        >>> # in_annotation=True, other flags False
        >>>
        >>> # Combined contexts - OR within filter
        >>> context = ResolverContext(mark_filter=True, in_or_context=True)
        >>> # Both flags active, JOIN stays LEFT despite filter marking
    Note:
        Context propagation is critical for correct query optimization. When
        traversing relationships, the context must be passed through the entire
        resolution chain to ensure consistent JOIN behavior.

        The immutable pattern (using replace() for modifications) prevents bugs
        from shared mutable state and makes context flow easier to reason about.
    """

    mark_filter: bool = False
    in_or_context: bool = False
    in_negation: bool = False
    in_order_by: bool = False
    in_annotation: bool = False
    has_nullable_check: bool = False

    def for_filter(self) -> ResolverContext:
        """Create context for filter operations with JOIN optimization enabled.

        Filter contexts mark JOINs as filter nodes, enabling INNER JOIN promotion
        when safe. This is the standard context for filter() and exclude() calls.

        Returns:
            New ResolverContext with mark_filter=True, other flags False.
        Example:
            >>> context = ResolverContext.for_filter()
            >>> # Used in: queryset.filter(category__name="Books")
            >>> # Result: JOIN marked as filter, eligible for INNER JOIN
        """
        return ResolverContext(mark_filter=True)

    def for_annotation(self) -> ResolverContext:
        """Create context for annotation operations tracking SELECT clause usage.

        Annotation contexts track which relationships are referenced in annotate()
        expressions. These JOINs must be included in the query even if not used
        in filters or ordering.

        Returns:
            New ResolverContext with in_annotation=True, other flags False.

        Example:
            >>> context = ResolverContext.for_annotation()
            >>> # Used in: queryset.annotate(author_name=F("book__author__name"))
            >>> # Result: JOIN tracked for SELECT clause inclusion
        """
        return ResolverContext(in_annotation=True)

    def with_or(self) -> ResolverContext:
        """Return copy with OR context enabled, preventing INNER JOIN promotion.

        OR contexts prevent INNER JOIN optimization because OR conditions require
        LEFT JOINs to preserve NULL rows for non-matching branches. Without this,
        rows would be incorrectly filtered out.

        Returns:
            New ResolverContext with in_or_context=True, other flags preserved.

        Example:
            >>> base_context = ResolverContext(mark_filter=True)
            >>> or_context = base_context.with_or()
            >>> # Used in: Q(category__name="Books") | Q(author__country="USA")
            >>> # Result: Both JOINs stay LEFT despite filter marking
        """
        return replace(self, in_or_context=True)

    def with_negation(self) -> ResolverContext:
        """Return copy with negation enabled, forcing LEFT JOIN for NULL semantics.

        Negation contexts force LEFT JOINs because NOT conditions need NULL rows
        to correctly identify non-matching relationships. INNER JOINs would
        incorrectly exclude rows without related objects.

        Returns:
            New ResolverContext with in_negation=True, other flags preserved.

        Example:
            >>> base_context = ResolverContext(mark_filter=True)
            >>> not_context = base_context.with_negation()
            >>> # Used in: ~Q(category__name="Books")
            >>> # Result: JOIN forced to LEFT to preserve NULL category rows
        """
        return replace(self, in_negation=True)
