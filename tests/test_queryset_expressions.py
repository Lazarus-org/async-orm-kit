from __future__ import annotations

import pytest
from sqlalchemy import ForeignKey, Integer, String, create_engine, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from async_orm_kit import Model
from async_orm_kit.exceptions import FilterError, InvalidLookupError, QueryStateError
from async_orm_kit.observers.queryset_observer import BaseQuerySetObserver
from async_orm_kit.query.aggregations import Count
from async_orm_kit.query.expressions import Case, F, Value, When
from async_orm_kit.query.postgres.optimizer import PostgresOptimizer
from async_orm_kit.query.q import Q
from async_orm_kit.queryset.queryset import QuerySet


class Phase3Profile(Model):
    __tablename__ = "phase3_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    score: Mapped[int] = mapped_column(Integer)


class Phase3User(Model):
    __tablename__ = "phase3_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    score: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(Integer)
    profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("phase3_profiles.id"), nullable=True
    )
    profile: Mapped[Phase3Profile | None] = relationship()


def queryset():
    # SQL compilation does not use the session; a dialect is supplied explicitly.
    return QuerySet(Phase3User, session=object())


def sql(query):
    statement = query._compiler.compile(query._state)
    return str(
        statement.compile(
            dialect=sqlite.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_queryset_clones_query_state_and_nested_annotation_paths():
    base = queryset()
    first = base.annotate(total=Count("id"))
    second = first.filter(score__gte=10)

    assert not base._state.filters
    assert not first._state.filters
    assert len(second._state.filters) == 1

    first._state.annotation_paths["total"].append("private_mutation")
    assert "private_mutation" not in second._state.annotation_paths["total"]


def test_state_property_returns_an_isolated_snapshot():
    query = queryset()
    snapshot = query.state
    snapshot.limit_val = 12
    snapshot.filters.append(Phase3User.score > 1)

    assert query._state.limit_val is None
    assert query._state.filters == []


def test_queryset_observer_registration_is_immutable_and_instance_based():
    class RecordingObserver(BaseQuerySetObserver):
        pass

    base = queryset()
    observed = base.add_observer(RecordingObserver())
    derived = observed.filter(score__gte=5)

    assert base.observers == ()
    assert len(observed.observers) == 1
    assert len(derived.observers) == 1
    assert observed.observers is not derived.observers

    with pytest.raises(TypeError, match="BaseQuerySetObserver instance"):
        base.add_observer(RecordingObserver)


def test_q_is_immutable_snapshots_mutable_lookup_values_and_is_hashable():
    values = [1, 2, 3]
    condition = Q(score__in=values)
    values.append(4)

    assert condition.children[0][2] == [1, 2, 3]
    assert hash(condition)

    exposed_values = condition.children[0][2]
    exposed_values.append(99)
    assert condition.children[0][2] == [1, 2, 3]

    with pytest.raises(AttributeError, match="immutable"):
        condition.connector = Q.OR

    with pytest.raises(AttributeError, match="immutable"):
        condition._children = ()


def test_q_composition_negation_and_collection_hashing_are_stable():
    left = Q(score__in=[1, 2])
    right = Q(name="Ada")
    combined = left | ~right

    assert combined.connector == Q.OR
    assert len(combined.children) == 2
    assert hash(combined)
    assert combined == (Q(score__in=[1, 2]) | ~Q(name="Ada"))


def test_exclude_accepts_q_objects_and_generates_negated_boolean_tree():
    query = queryset().exclude(Q(score__gt=10) | Q(name="blocked"))
    compiled = sql(query)

    assert "IS NOT 1" in compiled
    assert " OR " in compiled


def test_case_insensitive_exact_and_contains_are_supported():
    exact_sql = sql(queryset().filter(name__iexact="ada"))
    assert "lower(" in exact_sql.lower()
    assert " = lower(" in exact_sql.lower()


def test_case_insensitive_contains_is_supported():
    compiled = sql(queryset().filter(name__icontains="ada"))
    assert "lower(" in compiled.lower()
    assert "like" in compiled.lower()


def test_f_expression_values_are_resolved_with_filter_context():
    query = queryset().filter(score__gt=F("profile__score") + 1)
    node = query._state.join_graph.get_node("profile")

    assert node is not None
    assert node.used_in_filter is True
    assert "JOIN phase3_profiles" in sql(query)


def test_q_or_context_is_propagated_to_relationship_expression():
    query = queryset().filter(Q(profile__score__gte=90) | Q(score__gte=50))
    node = query._state.join_graph.get_node("profile")

    assert node is not None
    assert node.used_in_or_context is True
    assert "LEFT OUTER JOIN" in sql(query)


def test_annotated_ordering_uses_expression_not_raw_sql_text():
    query = queryset().annotate(total=Value(7)).order_by("-total")
    compiled = sql(query)

    assert "ORDER BY 7 DESC" in compiled
    assert "total DESC" not in compiled


def test_annotation_alias_must_be_safe_public_identifier():
    with pytest.raises(ValueError, match="public Python identifier"):
        queryset().annotate(**{"total DESC; DROP TABLE users": Value(1)})

    with pytest.raises(ValueError, match="conflicts with a mapped model attribute"):
        queryset().annotate(name=Value("replacement"))


def test_unsupported_annotation_values_are_rejected_early():
    with pytest.raises(TypeError, match="Unsupported annotation expression"):
        queryset().annotate(total="raw user-controlled SQL")


def test_values_without_fields_selects_all_mapped_columns():
    query = queryset().values()
    compiled = sql(query)

    assert "phase3_users.id" in compiled
    assert "phase3_users.name" in compiled
    assert "phase3_users.amount" in compiled
    assert query._state.values_list_select is None


def test_projection_methods_are_mutually_exclusive_and_last_call_wins():
    values_query = queryset().values("name").values_list("id", flat=True)
    assert values_query._state.values_select is None
    assert values_query._state.values_list_select is not None
    assert values_query._state.values_list_flat is True

    list_query = queryset().values_list("id", flat=True).values("name")
    assert list_query._state.values_select is not None
    assert list_query._state.values_list_select is None
    assert list_query._state.values_list_flat is False


def test_distinct_modes_are_mutually_exclusive():
    plain = queryset().distinct("name").distinct()
    assert plain._state.is_distinct is True
    assert plain._state.distinct_on == []

    on_fields = queryset().distinct().distinct("name")
    assert on_fields._state.is_distinct is False
    assert len(on_fields._state.distinct_on) == 1


def test_expression_ordering_is_supported_for_model_queries():
    query = queryset().order_by((F("score") * 2).desc())
    assert "ORDER BY phase3_users.score * 2 DESC" in sql(query)


def test_expression_ordering_is_rejected_for_projection_without_alias():
    with pytest.raises(QueryStateError, match="Expression-based ordering"):
        queryset().values("name").order_by(F("score").desc())


@pytest.mark.parametrize("field", ["-", "--name", ""])
def test_malformed_order_by_prefix_is_rejected(field):
    with pytest.raises(QueryStateError):
        queryset().order_by(field)


@pytest.mark.parametrize(
    ("lookup", "value"),
    [
        ("asc", 1),
        ("desc", 1),
        ("op", 1),
        ("collate", "NOCASE"),
    ],
)
def test_non_predicate_column_methods_cannot_be_used_as_lookups(lookup, value):
    with pytest.raises(InvalidLookupError):
        queryset().filter(**{f"score__{lookup}": value})


def test_range_lookup_requires_exactly_two_values():
    with pytest.raises(FilterError):
        queryset().filter(score__range=[1])


def test_isnull_lookup_requires_a_boolean():
    with pytest.raises(FilterError):
        queryset().filter(profile_id__isnull=1)


def test_in_lookup_rejects_strings_and_scalars():
    with pytest.raises(FilterError):
        queryset().filter(score__in="123")

    with pytest.raises(FilterError):
        queryset().filter(score__in=123)


def test_not_in_lookup_is_supported():
    compiled = sql(queryset().filter(score__not__in=[1, 2]))
    assert "NOT IN" in compiled


def test_value_and_field_expression_objects_are_immutable():
    expression = F("score")
    literal = Value(10)

    with pytest.raises(AttributeError, match="immutable"):
        expression.name = "amount"

    with pytest.raises(AttributeError, match="immutable"):
        literal.value = 20

    with pytest.raises(TypeError, match="supported scalar"):
        Value({"unsafe": "mutable"})


def test_case_when_expression_compiles_with_bound_values():
    query = queryset().annotate(
        grade=Case(
            When(score__gte=90, then=Value("A")),
            default=Value("B"),
        )
    )
    compiled = sql(query)

    assert "CASE WHEN" in compiled
    assert "THEN 'A'" in compiled
    assert "ELSE 'B'" in compiled


def test_filter_values_remain_bound_parameters_in_statement_compilation():
    query = queryset().filter(name="x' OR 1=1 --")
    statement = query._compiler.compile(query._state)
    compiled = statement.compile(dialect=sqlite.dialect())

    assert "x' OR 1=1 --" not in str(compiled)
    assert "name" in str(compiled)
    assert compiled.params


def test_jsonpath_expression_uses_a_bound_parameter():
    expression = PostgresOptimizer.jsonb_path_exists(
        Phase3User.__table__.c.name, "address.city"
    )
    statement = expression.self_group()
    compiled = statement.compile(dialect=postgresql.dialect())

    assert "$.address.city" not in str(compiled)
    assert "$.address.city" in compiled.params.values()

    rooted = PostgresOptimizer.jsonb_path_exists(
        Phase3User.__table__.c.name, "$.address.city"
    )
    rooted_compiled = rooted.compile(dialect=postgresql.dialect())
    assert "$.address.city" in rooted_compiled.params.values()
    with pytest.raises(ValueError, match="non-empty"):
        PostgresOptimizer.jsonb_path_exists(Phase3User.__table__.c.name, " ")


def test_query_slice_handles_empty_ranges_and_rejects_invalid_bounds():
    empty = queryset()[5:3]
    assert empty._state.limit_val == 0

    with pytest.raises(TypeError, match="slice bounds"):
        queryset()["1":3]

    with pytest.raises(TypeError, match="step"):
        queryset()[::1]

    with pytest.raises(ValueError, match="Negative"):
        queryset()[-1:]


def test_boolean_index_is_not_accepted_as_integer_index():
    with pytest.raises(TypeError, match="indices"):
        queryset()[True]


class _FakeResult:
    def fetchall(self):
        return [("query plan",)]


class _FakeAsyncConnection:
    def __init__(self):
        self.statement = None
        self.parameters = None

    async def exec_driver_sql(self, statement, parameters):
        self.statement = statement
        self.parameters = parameters
        return _FakeResult()


class _FakeAsyncSession:
    def __init__(self):
        from types import SimpleNamespace

        self.bind = SimpleNamespace(dialect=sqlite.dialect())
        self.connection_value = _FakeAsyncConnection()

    def get_bind(self):
        return self.bind

    async def connection(self):
        return self.connection_value


def test_async_explain_executes_parameterized_driver_sql():
    import asyncio

    session = _FakeAsyncSession()
    query = QuerySet(Phase3User, session).filter(name__in=["private-value", "other"])

    result = asyncio.run(query.explain())

    assert result == "query plan"
    assert session.connection_value.statement.startswith("EXPLAIN QUERY PLAN SELECT")
    assert "private-value" not in session.connection_value.statement
    assert isinstance(session.connection_value.parameters, tuple)
    assert "private-value" in session.connection_value.parameters


def test_async_explain_rejects_unsupported_sqlite_options():
    import asyncio

    session = _FakeAsyncSession()
    query = QuerySet(Phase3User, session)

    with pytest.raises(NotImplementedError, match="SQLite"):
        asyncio.run(query.explain(analyze=True))


class _FakeSyncConnection:
    def __init__(self):
        self.statement = None
        self.parameters = None

    def exec_driver_sql(self, statement, parameters):
        self.statement = statement
        self.parameters = parameters
        return _FakeResult()


class _FakeSyncSession:
    def __init__(self):
        from types import SimpleNamespace

        self.bind = SimpleNamespace(dialect=postgresql.dialect())
        self.connection_value = _FakeSyncConnection()

    def get_bind(self):
        return self.bind

    def connection(self):
        return self.connection_value


def test_postgres_explain_analyze_keeps_values_parameterized():
    session = _FakeSyncSession()
    statement = select(Phase3User).where(Phase3User.name == "private-value")

    result = PostgresOptimizer.explain_analyze(session, statement, verbose=True)

    assert result == "query plan"
    assert session.connection_value.statement.startswith(
        "EXPLAIN (ANALYZE, BUFFERS, VERBOSE)"
    )
    assert "private-value" not in session.connection_value.statement
    assert "private-value" in session.connection_value.parameters.values()


def test_public_sql_debug_output_uses_placeholders_not_bound_values():
    query = queryset().filter(name="private-value")
    rendered = query.sql(dialect=sqlite.dialect())

    assert "private-value" not in rendered
    assert "?" in rendered or ":" in rendered


class _SyncSessionAsyncAdapter:
    """Async facade used only to execute SQLAlchemy SQLite statements in unit tests."""

    def __init__(self, engine):
        self.bind = engine
        self.sync_session = Session(engine)

    async def execute(self, statement, params=None):
        return self.sync_session.execute(statement, params or {})

    async def close(self):
        self.sync_session.close()


def test_queryset_projection_and_expression_results_execute_on_sqlite():
    import asyncio

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Phase3Profile.__table__.create(engine)
    Phase3User.__table__.create(engine)

    with Session(engine) as seed:
        profile = Phase3Profile(score=95)
        seed.add(profile)
        seed.flush()
        profile_id = profile.id
        seed.add_all(
            [
                Phase3User(
                    name="Ada",
                    score=90,
                    amount=20,
                    profile_id=profile.id,
                ),
                Phase3User(
                    name="Grace",
                    score=80,
                    amount=30,
                    profile_id=profile.id,
                ),
                Phase3User(
                    name="Linus",
                    score=40,
                    amount=50,
                    profile_id=None,
                ),
            ]
        )
        seed.commit()

    adapter = _SyncSessionAsyncAdapter(engine)
    try:
        dictionaries = asyncio.run(
            QuerySet(Phase3User, adapter)
            .filter(name__icontains="a")
            .values("name", "score")
            .order_by("name")
            ._fetch()
        )
        assert dictionaries == [
            {"name": "Ada", "score": 90},
            {"name": "Grace", "score": 80},
        ]

        flat_names = asyncio.run(
            QuerySet(Phase3User, adapter)
            .values_list("name", flat=True)
            .order_by("name")
            ._fetch()
        )
        assert flat_names == ["Ada", "Grace", "Linus"]

        expression_results = asyncio.run(
            QuerySet(Phase3User, adapter)
            .filter(score__gt=F("amount"))
            .order_by("id")
            ._fetch()
        )
        assert [user.name for user in expression_results] == ["Ada", "Grace"]

        related_expression_results = asyncio.run(
            QuerySet(Phase3User, adapter)
            .filter(profile__score__gt=F("score"))
            .order_by("id")
            ._fetch()
        )
        assert [user.name for user in related_expression_results] == ["Ada", "Grace"]

        or_results = asyncio.run(
            QuerySet(Phase3User, adapter)
            .filter(Q(profile__score__gte=90) | Q(name="Linus"))
            .order_by("id")
            ._fetch()
        )
        assert [user.name for user in or_results] == ["Ada", "Grace", "Linus"]

        excluded_related = asyncio.run(
            QuerySet(Phase3User, adapter)
            .exclude(Q(profile__score__gte=90))
            .order_by("id")
            ._fetch()
        )
        assert [user.name for user in excluded_related] == ["Linus"]

        all_values = asyncio.run(
            QuerySet(Phase3User, adapter).values().order_by("id")._fetch()
        )
        assert [row["name"] for row in all_values] == ["Ada", "Grace", "Linus"]

        annotated = asyncio.run(
            QuerySet(Phase3User, adapter)
            .annotate(
                grade=Case(
                    When(score__gte=90, then=Value("A")),
                    default=Value("B"),
                )
            )
            .order_by("id")
            ._fetch()
        )
        assert [(user.name, user.grade) for user in annotated] == [
            ("Ada", "A"),
            ("Grace", "B"),
            ("Linus", "B"),
        ]

        grouped = asyncio.run(
            QuerySet(Phase3User, adapter)
            .values("profile_id")
            .annotate(user_count=Count("id"))
            .order_by("profile_id")
            ._fetch()
        )
        assert grouped == [
            {"profile_id": None, "user_count": 1},
            {"profile_id": profile_id, "user_count": 2},
        ]
    finally:
        asyncio.run(adapter.close())
        engine.dispose()


class _FakeStreamingResult:
    def __init__(self, rows, *, wait_after_first=False):
        import asyncio

        self._rows = iter(rows)
        self._wait_after_first = wait_after_first
        self._yielded = 0
        self.started_waiting = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    def unique(self):
        return self

    def scalars(self):
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):

        if self._wait_after_first and self._yielded >= 1:
            self.started_waiting.set()
            await self.release.wait()

        try:
            row = next(self._rows)
        except StopIteration:
            raise StopAsyncIteration from None

        self._yielded += 1
        return row

    async def close(self):
        self.closed = True


class _FakeStreamingSession:
    def __init__(self, result):
        self.result = result

    async def stream(self, statement):
        return self.result


def _streaming_executor(result):
    from types import SimpleNamespace

    from async_orm_kit.queryset.executor import QueryExecutor

    state = SimpleNamespace(
        model=Phase3User,
        values_select=None,
        values_list_select=None,
        values_list_flat=False,
        annotations={},
    )
    queryset = SimpleNamespace(
        model=Phase3User,
        state=state,
        compiler=SimpleNamespace(compile=lambda _state: object()),
        session=_FakeStreamingSession(result),
        observers=(),
    )
    return QueryExecutor(queryset)


def test_query_stream_closes_async_result_after_normal_completion():
    import asyncio

    records = [Phase3User(name="Ada", score=1, amount=1)]
    result = _FakeStreamingResult(records)
    executor = _streaming_executor(result)

    async def consume():
        return [item async for item in executor.stream()]

    assert asyncio.run(consume()) == records
    assert result.closed is True


def test_query_stream_closes_async_result_when_consumer_stops_early():
    import asyncio

    records = [
        Phase3User(name="Ada", score=1, amount=1),
        Phase3User(name="Grace", score=2, amount=2),
    ]
    result = _FakeStreamingResult(records)
    executor = _streaming_executor(result)

    async def consume_one():
        iterator = executor.stream()
        first = await anext(iterator)
        await iterator.aclose()
        return first

    first = asyncio.run(consume_one())
    assert first is records[0]
    assert result.closed is True


def test_query_stream_closes_async_result_when_consumer_is_cancelled():
    import asyncio

    records = [
        Phase3User(name="Ada", score=1, amount=1),
        Phase3User(name="Grace", score=2, amount=2),
    ]
    result = _FakeStreamingResult(records, wait_after_first=True)
    executor = _streaming_executor(result)

    async def consume_all():
        async for _item in executor.stream():
            pass

    async def scenario():
        task = asyncio.create_task(consume_all())
        await result.started_waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert result.closed is True


def test_queryset_exposes_explicit_stream_method_and_async_iteration():
    query = queryset()

    explicit_iterator = query.stream()
    implicit_iterator = query.__aiter__()

    assert hasattr(explicit_iterator, "__anext__")
    assert hasattr(implicit_iterator, "__anext__")
