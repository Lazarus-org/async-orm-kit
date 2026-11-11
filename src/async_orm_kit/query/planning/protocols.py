from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class JoinNodeReadable(Protocol):
    """Read-only view of a single join node."""

    @property
    def path(self) -> str: ...

    @property
    def join_type(self) -> str: ...

    @property
    def used_in_filter(self) -> bool: ...

    @property
    def used_in_or_context(self) -> bool: ...

    @property
    def has_nullable_check(self) -> bool: ...

    @property
    def used_in_negation(self) -> bool: ...

    @property
    def parent(self) -> JoinNodeReadable | None: ...

    @property
    def relationship(self) -> RelationshipReadable | None: ...


@runtime_checkable
class RelationshipReadable(Protocol):
    """What the planner needs to know about a relationship."""

    @property
    def uselist(self) -> bool: ...

    @property
    def lazy(self) -> str: ...


@runtime_checkable
class JoinGraphReadable(Protocol):
    """
    Read-only view of the join graph.

    The planner NEVER mutates the graph through this protocol.
    Mutation (like join promotion) goes through a separate
    mutable interface.
    """

    def has_node(self, path: str) -> bool: ...

    def get_node(self, path: str) -> JoinNodeReadable | None: ...

    @property
    def join_order(self) -> Sequence[JoinNodeReadable]: ...

    def resolve_relationship(self, path: str) -> RelationshipReadable | None: ...

    def resolve_relationship_path(
        self, path: str
    ) -> Sequence[RelationshipReadable] | None: ...


@runtime_checkable
class JoinGraphMutable(Protocol):
    """
    Narrow mutable interface — only for passes that need
    to modify join types (e.g., promotion pass).
    """

    def set_join_type(self, path: str, join_type: str) -> None: ...
