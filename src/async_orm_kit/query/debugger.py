"""Backward-compatible query diagnostics, backed by real SQLAlchemy events.

For concurrent async applications prefer ``QueryProfiler.capture()`` so each
request/task receives isolated statistics. The original global debugger and
manually started detector remain supported for development/CLI workflows.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from async_orm_kit._logging import get_logger
from async_orm_kit.query.observability import (
    QueryProfiler,
    QueryRecord,
    sanitized_sql,
    sql_fingerprint,
)

logger = get_logger(__name__)


class QueryDebugger:
    """Opt-in bounded SQL diagnostics with optional automatic event capture.

    ``enable()`` controls recording; ``attach(engine)`` connects SQLAlchemy
    cursor events. Without attachment, manual ``log_query()`` still works.
    Explicitly ``detach()`` after monitoring to remove engine listeners.

    This class is intentionally process-wide if reused globally. In async web
    handlers, use a fresh ``QueryProfiler`` and its ``capture()`` method.
    """

    def __init__(
        self, *, max_entries: int = 1000, slow_threshold_ms: float = 1000.0
    ) -> None:
        if (
            isinstance(max_entries, bool)
            or not isinstance(max_entries, int)
            or max_entries < 1
        ):
            raise ValueError("max_entries must be an integer >= 1")
        if slow_threshold_ms < 0:
            raise ValueError("slow_threshold_ms must be nonnegative")
        self.query_log: list[dict[str, Any]] = []
        self.enabled = False
        self._max_entries = max_entries
        self._slow_threshold_ms = slow_threshold_ms
        self._profiler: QueryProfiler | None = None
        self._engine: Engine | AsyncEngine | None = None
        self._lock = threading.RLock()

    def enable(self) -> None:
        self.enabled = True
        logger.info("Query debugging enabled")

    def disable(self) -> None:
        self.enabled = False
        logger.info("Query debugging disabled")

    def attach(self, engine: Engine | AsyncEngine) -> None:
        """Attach to an Engine/AsyncEngine (idempotent for the same engine)."""
        with self._lock:
            if self._engine is engine:
                return
            self.detach()
            self._profiler = QueryProfiler(engine, on_query=self._record_event)
            self._profiler.attach()
            self._engine = engine

    def detach(self) -> None:
        """Stop automatic event collection without changing enabled state."""
        with self._lock:
            if self._profiler is not None:
                self._profiler.detach()
            self._profiler = None
            self._engine = None

    def _record_event(self, record: QueryRecord) -> None:
        self.log_query(
            record.sql,
            record.duration_ms / 1000,
            record.row_count,
            success=record.success,
            error_type=record.error_type,
        )

    def log_query(
        self,
        sql: str,
        duration: float,
        row_count: int | None,
        *,
        success: bool = True,
        error_type: str | None = None,
    ) -> None:
        """Record one cursor execution; duration is expressed in seconds."""
        if not self.enabled:
            return
        duration_ms = round(duration * 1000, 3)
        safe_sql = sanitized_sql(sql)
        entry = {
            "sql": safe_sql,
            "duration_ms": duration_ms,
            "row_count": row_count
            if row_count is not None and row_count >= 0
            else None,
            "timestamp": time.time(),
            "success": success,
            "error_type": error_type,
        }
        with self._lock:
            if len(self.query_log) >= self._max_entries:
                self.query_log.pop(0)
            self.query_log.append(entry)
        if duration_ms >= self._slow_threshold_ms:
            logger.warning(
                "Slow query detected", duration_ms=duration_ms, sql=safe_sql[:200]
            )

    def get_stats(self) -> dict[str, Any]:
        """Return summary statistics for the retained history."""
        with self._lock:
            entries = list(self.query_log)
        if not entries:
            return {
                "total_queries": 0,
                "total_duration_ms": 0,
                "avg_duration_ms": 0,
                "slowest_query": None,
            }
        total = sum(entry["duration_ms"] for entry in entries)
        slowest = max(entries, key=lambda entry: entry["duration_ms"])
        return {
            "total_queries": len(entries),
            "total_duration_ms": round(total, 2),
            "avg_duration_ms": round(total / len(entries), 2),
            "slowest_query": {
                "sql": slowest["sql"][:200],
                "duration_ms": slowest["duration_ms"],
            },
        }

    def clear(self) -> None:
        with self._lock:
            self.query_log.clear()

    @contextmanager
    def measure_query(self, description: str = "") -> Iterator[None]:
        """Time a Python block for logging (not a DB cursor execution)."""
        start = time.perf_counter()
        try:
            yield
        finally:
            if self.enabled:
                logger.debug(
                    "Query block measured",
                    description=description,
                    duration_ms=round((time.perf_counter() - start) * 1000, 2),
                )


query_debugger = QueryDebugger()


class NPlusOneDetector:
    """Heuristically identify repeated SELECT/WHERE SQL fingerprints.

    This cannot prove a relationship-level N+1 issue; it finds likely candidates
    for inspection. Attach an engine to receive real executions or manually
    feed ``record_query()`` for backward compatibility. Use QueryProfile's
    n_plus_one_report() for request-scoped async observations instead.
    """

    def __init__(self, *, threshold: int = 10) -> None:
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, int)
            or threshold < 2
        ):
            raise ValueError("threshold must be an integer >= 2")
        self.query_count = 0
        self.query_patterns: dict[str, int] = {}
        self.monitoring = False
        self.threshold = threshold
        self._profiler: QueryProfiler | None = None
        self._engine: Engine | AsyncEngine | None = None
        self._lock = threading.RLock()

    def start_monitoring(self) -> None:
        with self._lock:
            self.monitoring = True
            self.query_count = 0
            self.query_patterns.clear()
        logger.info("N+1 monitoring started")

    def stop_monitoring(self) -> None:
        self.monitoring = False

    def attach(self, engine: Engine | AsyncEngine) -> None:
        """Install event monitoring for executed SQL statements."""
        with self._lock:
            if self._engine is engine:
                return
            self.detach()
            self._profiler = QueryProfiler(engine, on_query=self._record_event)
            self._profiler.attach()
            self._engine = engine

    def detach(self) -> None:
        with self._lock:
            if self._profiler is not None:
                self._profiler.detach()
            self._profiler = None
            self._engine = None

    def _record_event(self, record: QueryRecord) -> None:
        if record.success:
            self.record_query(record.sql)

    def record_query(self, sql: str) -> None:
        if not self.monitoring:
            return
        pattern = self._normalize_sql(sql)
        with self._lock:
            self.query_count += 1
            self.query_patterns[pattern] = self.query_patterns.get(pattern, 0) + 1

    def _normalize_sql(self, sql: str) -> str:
        return sql_fingerprint(sql)

    def report(self) -> dict[str, Any]:
        with self._lock:
            patterns = dict(self.query_patterns)
            count = self.query_count
        suspects: list[dict[str, Any]] = [
            {
                "pattern": pattern[:200],
                "count": occurrences,
                "severity": "high" if occurrences > 100 else "medium",
            }
            for pattern, occurrences in patterns.items()
            if occurrences >= self.threshold
            and pattern.lstrip().upper().startswith("SELECT ")
            and " WHERE " in f" {pattern.upper()} "
        ]
        suspects.sort(key=lambda x: (-x["count"], x["pattern"]))
        result = {
            "total_queries": count,
            "unique_patterns": len(patterns),
            "potential_n_plus_one": suspects,
        }
        if suspects:
            logger.warning(
                "Potential repeated-query patterns detected",
                patterns=len(suspects),
                total_queries=count,
            )
        return result


__all__ = ["QueryDebugger", "NPlusOneDetector", "query_debugger"]
