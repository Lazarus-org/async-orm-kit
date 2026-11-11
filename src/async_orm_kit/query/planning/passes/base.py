from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..directives import LoadDirective, QueryContext
from ..protocols import JoinGraphReadable


@dataclass
class PassResult:
    """
    Every pass returns a PassResult so the orchestrator
    can log, audit, and test each pass independently.
    """

    pass_name: str
    directives: list[LoadDirective] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mutations: list[str] = field(default_factory=list)  # for audit

    @property
    def had_effect(self) -> bool:
        return bool(self.directives or self.warnings or self.mutations)


class PlanningPass(ABC):
    """
    Abstract base for a single planning pass.

    Each pass:
    - Receives the current state (graph + context + prior directives)
    - Returns a PassResult describing what it did
    - Does NOT hold mutable state between calls (stateless)
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def execute(
        self,
        graph: JoinGraphReadable,
        context: QueryContext,
        current_directives: list[LoadDirective],
    ) -> PassResult: ...
