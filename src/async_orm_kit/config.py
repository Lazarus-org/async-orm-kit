"""Configuration primitives for async-orm-kit.

Configuration is explicit and immutable. Applications can create separate
ORMConfig instances for independent query policies without relying on global
project settings.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ORMConfig:
    """Runtime configuration for ORM query compilation.

    Args:
        max_joinedload_depth: Maximum relationship depth kept in a joined eager
            loading chain before the planner prefers select-in loading.
    """

    max_joinedload_depth: int = 2

    def __post_init__(self) -> None:
        if isinstance(self.max_joinedload_depth, bool) or not isinstance(
            self.max_joinedload_depth, int
        ):
            raise TypeError("max_joinedload_depth must be an integer")
        if self.max_joinedload_depth < 1:
            raise ValueError("max_joinedload_depth must be at least 1")


DEFAULT_CONFIG = ORMConfig()
