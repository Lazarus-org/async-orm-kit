from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class LoadStrategy(Enum):
    JOINEDLOAD = auto()
    SELECTINLOAD = auto()
    SUBQUERYLOAD = auto()
    SKIP = auto()


@dataclass(frozen=True, slots=True)
class LoadDirective:
    """
    Immutable output of the planning process.

    One directive per loading path, carrying the chosen strategy
    and a human-readable reason for debugging / EXPLAIN output.
    """

    path: str
    strategy: LoadStrategy
    reason: str
    original_strategy: LoadStrategy | None = None  # before any demotion
    segment_strategies: tuple[LoadStrategy, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise ValueError("LoadDirective.path must be a non-empty string.")
        path_parts = self.path.split("__")
        if any(not part or part.strip() != part for part in path_parts):
            raise ValueError("LoadDirective.path contains an invalid segment.")
        if not isinstance(self.strategy, LoadStrategy):
            raise TypeError("LoadDirective.strategy must be a LoadStrategy.")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("LoadDirective.reason must be a non-empty string.")
        if self.original_strategy is not None and not isinstance(
            self.original_strategy, LoadStrategy
        ):
            raise TypeError("original_strategy must be a LoadStrategy or None.")
        try:
            segment_strategies = tuple(self.segment_strategies)
        except TypeError as exc:
            raise TypeError(
                "segment_strategies must be an iterable of strategies."
            ) from exc
        object.__setattr__(self, "segment_strategies", segment_strategies)
        if segment_strategies:
            if len(segment_strategies) != len(path_parts):
                raise ValueError(
                    "segment_strategies must contain one strategy per path segment."
                )
            if any(
                not isinstance(strategy, LoadStrategy)
                for strategy in segment_strategies
            ):
                raise TypeError("segment_strategies must contain LoadStrategy values.")

    @property
    def was_demoted(self) -> bool:
        return (
            self.original_strategy is not None
            and self.original_strategy != self.strategy
        )

    def __repr__(self) -> str:
        demoted = " (demoted)" if self.was_demoted else ""
        return (
            f"LoadDirective({self.path!r}, "
            f"{self.strategy.name}{demoted}, "
            f"{self.reason!r})"
        )


@dataclass(frozen=True, slots=True)
class JoinPromotion:
    """Record of a LEFT → INNER promotion for audit trail."""

    path: str
    old_type: str
    new_type: str
    reason: str


@dataclass(frozen=True, slots=True)
class QueryContext:
    """
    Immutable snapshot of query-level facts that planning
    passes need to make decisions.

    Built ONCE by the QuerySet compiler, then passed through
    read-only. No pass mutates this.
    """

    has_aggregates: bool = False
    is_distinct: bool = False
    limit: int | None = None

    # Paths the user explicitly requested
    select_related_paths: frozenset[str] = field(default_factory=frozenset)
    prefetch_paths: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if type(self.has_aggregates) is not bool or type(self.is_distinct) is not bool:
            raise TypeError("QueryContext flags must be bool values.")
        if self.limit is not None and (type(self.limit) is not int or self.limit < 0):
            raise ValueError(
                "QueryContext.limit must be a non-negative integer or None."
            )

        for attribute in ("select_related_paths", "prefetch_paths"):
            paths = frozenset(getattr(self, attribute))
            if any(not isinstance(path, str) or not path for path in paths):
                raise ValueError(f"{attribute} must contain non-empty strings.")
            object.__setattr__(self, attribute, paths)
