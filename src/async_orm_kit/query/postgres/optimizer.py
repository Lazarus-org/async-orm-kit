from __future__ import annotations

from typing import Any

from sqlalchemy import cast, exists, inspect as sa_inspect, literal, select
from sqlalchemy.dialects.postgresql import JSONPATH

from async_orm_kit._logging import get_logger

logger = get_logger(__name__)


class PostgresOptimizer:
    """
    PostgreSQL-specific query optimizations.

    Features:
    - LATERAL joins for top-N per group
    - EXISTS optimization for relationship filters
    - JSONB operator support
    - Array operations
    """

    @staticmethod
    def is_postgres(session) -> bool:
        """Check if current database is PostgreSQL"""
        dialect_name = session.bind.dialect.name
        return dialect_name == "postgresql"

    @staticmethod
    def lateral_join_top_n(
        base_model: type,
        related_model: type,
        relationship_fk: str,
        order_by_field: str,
        limit: int = 1,
        descending: bool = True,
    ):
        """
        Create LATERAL join for top-N per parent.

        Example: Get latest post per user

        Usage:
            lateral_subq = PostgresOptimizer.lateral_join_top_n(
                User, Post, "user_id", "created_at", limit=1
            )
            stmt = select(User).join(lateral_subq, isouter=True)
        """
        primary_key: Any = sa_inspect(base_model).primary_key
        if len(primary_key) != 1:
            raise ValueError("lateral_join_top_n requires a single-column primary key")
        order_col = getattr(related_model, order_by_field)
        fk_col = getattr(related_model, relationship_fk)

        direction = order_col.desc() if descending else order_col.asc()

        lateral_subq = (
            select(related_model)
            .where(fk_col == getattr(base_model, primary_key[0].key))
            .order_by(direction)
            .limit(limit)
            .lateral()
        )

        return lateral_subq

    @staticmethod
    def optimize_exists_filter(base_model: type, relationship_attr, condition):
        """
        Use EXISTS instead of JOIN for relationship filters.

        More efficient for large collections.

        Usage:
            exists_cond = PostgresOptimizer.optimize_exists_filter(
                User, User.posts, Post.published == True
            )
            stmt = select(User).where(exists_cond)
        """
        related_model = relationship_attr.property.mapper.class_

        exists_subq = (
            exists()
            .where(
                getattr(
                    related_model,
                    next(iter(relationship_attr.property.remote_side)).name,
                )
                == getattr(
                    base_model, relationship_attr.property.local_remote_pairs[0][0].name
                )
            )
            .where(condition)
        )

        return exists_subq

    @staticmethod
    def jsonb_contains(column, value: dict):
        """
        PostgreSQL JSONB @> operator.

        Usage:
            User.metadata.op('@>')({"role": "admin"})
        """
        return column.op("@>")(value)

    @staticmethod
    def jsonb_path_exists(column, path: str):
        """
        PostgreSQL JSONB path query.

        Usage:
            jsonb_path_exists(User.data, '$.address.city')
        """
        if not isinstance(path, str) or not path.strip():
            raise ValueError("JSONPath must be a non-empty string.")
        normalized_path = path.strip()
        if not normalized_path.startswith("$"):
            normalized_path = f"$.{normalized_path}"
        # Bind the path as data. Never interpolate a caller-controlled path into SQL.
        return column.op("@?")(cast(literal(normalized_path), JSONPATH))

    @staticmethod
    def array_contains(column, value):
        """
        PostgreSQL array @> operator.

        Usage:
            User.tags.op('@>')(["python", "sql"])
        """
        return column.op("@>")(value)

    @staticmethod
    def explain_analyze(session, stmt, verbose: bool = False) -> str:
        """Run parameterized EXPLAIN ANALYZE on PostgreSQL.

        Values remain DBAPI parameters. This avoids executing SQL produced by
        interpolating literal values into a text statement.
        """
        if type(verbose) is not bool:
            raise TypeError("verbose must be a bool.")
        if not PostgresOptimizer.is_postgres(session):
            logger.warning("EXPLAIN ANALYZE only available on PostgreSQL")
            return ""

        bind = session.get_bind()
        compiled = stmt.compile(
            dialect=bind.dialect,
            compile_kwargs={"render_postcompile": True},
        )
        options = ["ANALYZE", "BUFFERS"]
        if verbose:
            options.append("VERBOSE")

        parameters = (
            tuple(compiled.params[name] for name in compiled.positiontup)
            if compiled.positional
            else compiled.params
        )
        result = session.connection().exec_driver_sql(
            f"EXPLAIN ({', '.join(options)}) {compiled.string}",
            parameters,
        )
        rows = result.fetchall()
        return "\n".join(str(row[0]) for row in rows)

    @staticmethod
    def suggest_index(session, stmt) -> list[str]:
        """
        Analyze query plan and suggest missing indexes.

        Returns list of suggested CREATE INDEX statements.
        """
        if not PostgresOptimizer.is_postgres(session):
            return []

        plan = PostgresOptimizer.explain_analyze(session, stmt)
        suggestions = []

        # Simple heuristic: look for Seq Scan in plan
        if "Seq Scan" in plan:
            logger.warning("Sequential scan detected - consider adding index")
            # Parse table and filter conditions from plan
            # This is simplified - production version would parse EXPLAIN output
            suggestions.append("-- Analyze EXPLAIN output and add appropriate indexes")

        return suggestions
