"""Opt-in SQLAlchemy execution diagnostics, scoped to the current async task.

The profiler measures DBAPI cursor-execution time, not complete QuerySet
materialization, network connection acquisition, or request latency. It works
with SQLAlchemy ``Engine`` and ``AsyncEngine`` without wrapping/monkey-patching
SQLAlchemy internals and introduces no new mandatory dependencies.
"""

from __future__ import annotations

import re
import threading
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from async_orm_kit._logging import get_logger

logger = get_logger(__name__)

# Only SQL templates are retained. Redact SQL literals and comments even when
# raw SQL is executed through exec_driver_sql(). Parameter values are never read.
_SQL_LITERAL = re.compile(r"'(?:''|[^'])*'", re.DOTALL)
_SQL_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_SQL_LINE_COMMENT = re.compile(r"--[^\n]*")
_SQL_NUMBER = re.compile(r"(?<![\w:$])\b\d+(?:\.\d+)?\b(?![\w])")
_SQL_WHITESPACE = re.compile(r"\s+")


def sanitized_sql(sql: str, *, max_length: int = 2048) -> str:
    """Return a bounded, best-effort redacted statement (never bind values).

    For absolute secrecy in production, do not enable SQL recording: arbitrary
    driver SQL and SQL identifiers may contain confidential information.
    """
    text = _SQL_LITERAL.sub("'?'", sql)
    text = _SQL_BLOCK_COMMENT.sub(" ", text)
    text = _SQL_LINE_COMMENT.sub(" ", text)
    text = _SQL_NUMBER.sub("?", text)
    text = _SQL_WHITESPACE.sub(" ", text).strip()
    return text[:max_length]


def sql_fingerprint(sql: str) -> str:
    """Normalize a statement for *heuristic* repetition detection."""
    return sanitized_sql(sql, max_length=4096)


@dataclass(frozen=True, slots=True)
class QueryRecord:
    """One database cursor execution, without SQL bind parameters."""

    sql: str
    duration_ms: float
    timestamp: float
    row_count: int | None
    success: bool
    error_type: str | None = None


@dataclass(slots=True)
class QueryProfile:
    """A bounded task-local capture, including totals for evicted records."""

    max_entries: int
    slow_threshold_ms: float
    records: list[QueryRecord] = field(default_factory=list)
    total_queries: int = 0
    total_duration_ms: float = 0.0
    slow_queries: int = 0
    failed_queries: int = 0
    dropped_entries: int = 0
    untracked_patterns: int = 0
    patterns: Counter[str] = field(default_factory=Counter)
    active: bool = True
    _slowest: QueryRecord | None = field(default=None, repr=False)

    def _record(self, record: QueryRecord) -> None:
        if not self.active:
            return
        self.total_queries += 1
        self.total_duration_ms += record.duration_ms
        self.slow_queries += record.duration_ms >= self.slow_threshold_ms
        self.failed_queries += not record.success
        if self._slowest is None or record.duration_ms > self._slowest.duration_ms:
            self._slowest = record
        if record.success:
            fingerprint = sql_fingerprint(record.sql)
            if fingerprint in self.patterns or len(self.patterns) < self.max_entries:
                self.patterns[fingerprint] += 1
            else:
                # Query patterns are bounded too; distinct raw SQL must not
                # grow the profile indefinitely in a long-running request.
                self.untracked_patterns += 1
        if len(self.records) >= self.max_entries:
            self.records.pop(0)
            self.dropped_entries += 1
        self.records.append(record)

    def stats(self) -> dict[str, Any]:
        """Return full execution totals, even if older records were evicted."""
        return {
            "total_queries": self.total_queries,
            "total_duration_ms": round(self.total_duration_ms, 3),
            "avg_duration_ms": round(self.total_duration_ms / self.total_queries, 3)
            if self.total_queries
            else 0.0,
            "slow_queries": self.slow_queries,
            "failed_queries": self.failed_queries,
            "dropped_entries": self.dropped_entries,
            "untracked_patterns": self.untracked_patterns,
            "slowest_query": (
                {"sql": self._slowest.sql, "duration_ms": self._slowest.duration_ms}
                if self._slowest is not None
                else None
            ),
        }

    def n_plus_one_report(self, *, threshold: int = 10) -> dict[str, Any]:
        """Report suspected repeated SELECT-by-key patterns, not guaranteed N+1.

        Repeated WHERE queries are only a heuristic; deliberate polling and
        bulk jobs may also match. This does not trace object relationships.
        """
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, int)
            or threshold < 2
        ):
            raise ValueError("threshold must be an integer >= 2")
        suspects: list[dict[str, Any]] = [
            {
                "pattern": sql[:200],
                "count": count,
                "severity": "high" if count > 100 else "medium",
            }
            for sql, count in self.patterns.items()
            if count >= threshold
            and sql.lstrip().upper().startswith("SELECT ")
            and " WHERE " in f" {sql.upper()} "
        ]
        suspects.sort(key=lambda entry: (-entry["count"], entry["pattern"]))
        return {
            "total_queries": self.total_queries,
            "unique_patterns": len(self.patterns),
            "untracked_patterns": self.untracked_patterns,
            "potential_n_plus_one": suspects,
        }


class QueryProfiler:
    """Capture cursor executions from a SQLAlchemy Engine or AsyncEngine.

    A profiler's ``capture()`` is scoped via ``ContextVar`` to the current
    execution context; captures in concurrent async tasks do not mix. Nested
    captures both receive the query. Lifecycle registration is reference-counted
    so one exiting request cannot detach another active request's listener.

    Example::

        profiler = QueryProfiler(async_engine)
        with profiler.capture() as profile:
            results = await session.execute(statement)
        print(profile.stats(), profile.n_plus_one_report(threshold=3))

    ``capture()`` automatically registers and removes listeners. Use ``attach``
    and ``detach`` for persistent monitoring, though uncaptured executions are
    ignored unless an ``on_query`` callback is configured.
    """

    def __init__(
        self,
        engine: Engine | AsyncEngine,
        *,
        max_entries: int = 1000,
        slow_threshold_ms: float = 100.0,
        on_query: Callable[[QueryRecord], None] | None = None,
    ) -> None:
        sync_engine = engine.sync_engine if isinstance(engine, AsyncEngine) else engine
        if not isinstance(sync_engine, Engine):
            raise TypeError("engine must be a SQLAlchemy Engine or AsyncEngine")
        if (
            isinstance(max_entries, bool)
            or not isinstance(max_entries, int)
            or max_entries < 1
        ):
            raise ValueError("max_entries must be an integer >= 1")
        if slow_threshold_ms < 0:
            raise ValueError("slow_threshold_ms must be nonnegative")
        if on_query is not None and not callable(on_query):
            raise TypeError("on_query must be callable")
        self._engine = sync_engine
        self._max_entries = max_entries
        self._slow_threshold_ms = float(slow_threshold_ms)
        self._on_query = on_query
        self._captures: ContextVar[tuple[QueryProfile, ...]] = ContextVar(
            f"ormkit_query_profiles_{id(self)}", default=()
        )
        self._registered = False
        self._permanent = False
        self._capture_count = 0
        self._lock = threading.RLock()
        self._clock_key = f"_ormkit_query_start_{id(self)}"
        # Hold exact callback objects for event.remove(), including on Python
        # versions that create a fresh bound method object on attribute access.
        self._before_cb = self._before_cursor_execute
        self._after_cb = self._after_cursor_execute
        self._error_cb = self._handle_error

    def _install(self) -> None:
        if self._registered:
            return
        event.listen(self._engine, "before_cursor_execute", self._before_cb)
        event.listen(self._engine, "after_cursor_execute", self._after_cb)
        event.listen(self._engine, "handle_error", self._error_cb)
        self._registered = True

    def _uninstall(self) -> None:
        if not self._registered:
            return
        event.remove(self._engine, "before_cursor_execute", self._before_cb)
        event.remove(self._engine, "after_cursor_execute", self._after_cb)
        event.remove(self._engine, "handle_error", self._error_cb)
        self._registered = False

    def attach(self) -> None:
        """Keep listeners installed until ``detach()`` is called."""
        with self._lock:
            self._permanent = True
            self._install()

    def detach(self) -> None:
        """Remove persistent monitoring without disrupting active captures."""
        with self._lock:
            self._permanent = False
            if not self._capture_count:
                self._uninstall()

    @contextmanager
    def capture(self) -> Iterator[QueryProfile]:
        """Capture queries in this task; usable across ``await`` boundaries."""
        profile = QueryProfile(
            max_entries=self._max_entries,
            slow_threshold_ms=self._slow_threshold_ms,
        )
        with self._lock:
            self._install()
            self._capture_count += 1
        token = self._captures.set((*self._captures.get(), profile))
        try:
            yield profile
        finally:
            profile.active = False
            self._captures.reset(token)
            with self._lock:
                self._capture_count -= 1
                if not self._permanent and not self._capture_count:
                    self._uninstall()

    @asynccontextmanager
    async def acapture(self) -> AsyncIterator[QueryProfile]:
        """``async with`` equivalent of ``capture()``."""
        with self.capture() as profile:
            yield profile

    def _before_cursor_execute(
        self,
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if self._captures.get() or self._on_query is not None:
            setattr(context, self._clock_key, time.perf_counter_ns())

    def _finish(
        self,
        statement: str,
        context: Any,
        *,
        row_count: int | None = None,
        error_type: str | None = None,
    ) -> None:
        start = getattr(context, self._clock_key, None)
        if start is None:
            return
        # Avoid double counting if an adapter emits more than one callback.
        delattr(context, self._clock_key)
        duration_ms = max(0.0, (time.perf_counter_ns() - start) / 1_000_000)
        record = QueryRecord(
            sql=sanitized_sql(statement),
            duration_ms=duration_ms,
            timestamp=time.time(),
            row_count=row_count if row_count is not None and row_count >= 0 else None,
            success=error_type is None,
            error_type=error_type,
        )
        for profile in self._captures.get():
            profile._record(record)
        if self._on_query is not None:
            try:
                self._on_query(record)
            except Exception:
                # Diagnostics must never change the result of a DB operation.
                logger.exception("Query monitoring callback failed")

    def _after_cursor_execute(
        self,
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        self._finish(statement, context, row_count=getattr(cursor, "rowcount", None))

    def _handle_error(self, exception_context: Any) -> None:
        context = getattr(exception_context, "execution_context", None)
        if context is None:
            return  # failure before cursor execution is outside this profiler
        error = getattr(exception_context, "original_exception", None)
        self._finish(
            getattr(exception_context, "statement", "") or "",
            context,
            error_type=type(error).__name__ if error is not None else "UnknownError",
        )


__all__ = [
    "QueryProfiler",
    "QueryProfile",
    "QueryRecord",
    "sanitized_sql",
    "sql_fingerprint",
]
