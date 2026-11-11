"""Safe correlated subquery expressions for Django-inspired QuerySets.

OuterRef markers are never rendered as user-supplied SQL. They are replaced by
validated, mapped outer columns while a Subquery or Exists expression resolves.
Outside a containing subquery, unresolved markers are rejected by QueryCompiler.
"""

from __future__ import annotations

from sqlalchemy import column, inspect as sa_inspect
from sqlalchemy.sql import visitors

from async_orm_kit.exceptions import QueryStateError
from async_orm_kit.query.lookups.resolver import ResolverContext
from async_orm_kit.query.mixins.combinable import CombinableMixin
from async_orm_kit.query.mixins.orderable import OrderableMixin

_OUTER_REF = "_async_orm_kit_outer_ref"


class OuterRef(CombinableMixin, OrderableMixin):
    """Reference a mapped field on the *containing* queryset's model.

    Example::

        posts = Post.objects.filter(session, author_id=OuterRef('id'))
        authors = Author.objects.annotate(session, newest=Subquery(
            posts.order_by('-id').values_list('title', flat=True)[:1]
        ))
    """

    name: str
    depth: int
    __slots__ = ("name", "depth")

    def __init__(self, name: str | OuterRef):
        if isinstance(name, OuterRef):
            # Django-style OuterRef(OuterRef("id")) skips the immediate
            # containing queryset and resolves against its parent instead.
            self.name = name.name
            self.depth = name.depth + 1
        elif isinstance(name, str) and name and all(name.split("__")):
            self.name = name
            self.depth = 1
        else:
            raise ValueError(
                "OuterRef requires a non-empty mapped field path or another OuterRef"
            )

    def resolve(self, field_resolver, context: ResolverContext = ResolverContext()):
        # Store the depth in SQLAlchemy's annotation metadata, never SQL text.
        # Each containing Subquery/Exists consumes one correlation boundary.
        return _marker(self.name, self.depth)


def _marker(name: str, depth: int):
    return column("__async_orm_outer_ref__")._annotate({_OUTER_REF: (name, depth)})


class _QueryExpression(CombinableMixin, OrderableMixin):
    __slots__ = ("queryset",)

    def __init__(self, queryset):
        from async_orm_kit.queryset.queryset import QuerySet

        if not isinstance(queryset, QuerySet):
            raise TypeError("Subquery/Exists requires a QuerySet")
        self.queryset = queryset

    def _compile(self, outer_resolver, context: ResolverContext, *, scalar: bool):
        state = self.queryset._state.clone()
        state.select_related.clear()
        state.prefetch_related.clear()
        state.for_update = None
        state.order_by = state.order_by if scalar else []
        if scalar:
            if state.values_select is not None:
                if len(state.values_select) != 1:
                    raise QueryStateError("Subquery requires a single selected column")
                expression = state.values_select[0][1]
            elif state.values_list_select is not None:
                if len(state.values_list_select) != 1:
                    raise QueryStateError("Subquery requires a single selected column")
                expression = state.values_list_select[0][1]
            else:
                if state.annotations or state.has_aggregates:
                    raise QueryStateError(
                        "Annotated Subquery requires explicit scalar projection"
                    )
                primary_key = sa_inspect(self.queryset.model).primary_key
                if len(primary_key) != 1:
                    raise QueryStateError(
                        "Composite-key Subquery requires explicit scalar projection"
                    )
                expression = getattr(self.queryset.model, primary_key[0].key)
        else:
            expression = None
            state.order_by.clear()

        # Referring to the same mapped class on both sides of a correlation
        # produces SQL such as "WHERE authors.id = authors.id". SQLAlchemy
        # cannot distinguish the inner/outer identity unless the inner model
        # is explicitly aliased. Reject the ambiguous form before rendering it.
        if self.queryset.model is outer_resolver.model:
            clauses = (
                list(state.filters)
                + list(state.having_conditions)
                + list(state.annotations.values())
                + list(state.order_by)
            )
            clauses.extend(expr for _, expr in (state.values_select or ()))
            clauses.extend(expr for _, expr in (state.values_list_select or ()))
            for clause in clauses:
                for element in visitors.iterate(clause):
                    reference = getattr(element, "_annotations", {}).get(_OUTER_REF)
                    if reference is None:
                        continue
                    depth = reference[1] if isinstance(reference, tuple) else 1
                    if depth == 1:
                        raise QueryStateError(
                            "Self-correlated queries require a distinct inner "
                            "SQLAlchemy aliased(Model) entity"
                        )

        def replace(element):
            reference = getattr(element, "_annotations", {}).get(_OUTER_REF)
            if reference is None:
                return None
            name, depth = reference if isinstance(reference, tuple) else (reference, 1)
            if depth > 1:
                return _marker(name, depth - 1)
            return outer_resolver.resolve_to_column(name, context=context).column

        def resolve_in(expression):
            # Replacing references on the entire compiled SELECT forces
            # SQLAlchemy to clone ORM JOIN aliases. A selected aggregate could
            # then reference one alias while FROM uses another, causing a
            # Cartesian product or an ambiguous column error. Resolve markers
            # in the query state BEFORE the compiler constructs its FROM/JOIN
            # graph, and leave unrelated column expressions intact.
            if not any(
                getattr(element, "_annotations", {}).get(_OUTER_REF) is not None
                for element in visitors.iterate(expression)
            ):
                return expression
            return visitors.replacement_traverse(expression, {}, replace)

        state.filters = [resolve_in(expr) for expr in state.filters]
        state.having_conditions = [resolve_in(expr) for expr in state.having_conditions]
        state.annotations = {
            key: resolve_in(expr) for key, expr in state.annotations.items()
        }
        state.values_select = (
            [(key, resolve_in(expr)) for key, expr in state.values_select]
            if state.values_select is not None
            else None
        )
        state.values_list_select = (
            [(key, resolve_in(expr)) for key, expr in state.values_list_select]
            if state.values_list_select is not None
            else None
        )
        state.order_by = [resolve_in(expr) for expr in state.order_by]
        state.group_by_fields = [resolve_in(expr) for expr in state.group_by_fields]
        state.distinct_on = [
            resolve_in(expr) if not isinstance(expr, str) else expr
            for expr in state.distinct_on
        ]

        statement = self.queryset._compiler.compile(state, allow_outer_refs=True)
        if scalar:
            if state.values_select is not None:
                expression = state.values_select[0][1]
            elif state.values_list_select is not None:
                expression = state.values_list_select[0][1]
            statement = statement.with_only_columns(
                expression, maintain_column_froms=True
            )
        return statement


class Subquery(_QueryExpression):
    """Use a single-column QuerySet as a scalar SQL subquery."""

    def resolve(self, field_resolver, context: ResolverContext = ResolverContext()):
        return self._compile(field_resolver, context, scalar=True).scalar_subquery()


class Exists(_QueryExpression):
    """Use a QuerySet as an automatically correlated EXISTS predicate."""

    __slots__ = ("negated",)

    def __init__(self, queryset, *, negated: bool = False):
        super().__init__(queryset)
        self.negated = negated

    def __invert__(self):
        return Exists(self.queryset, negated=not self.negated)

    def resolve(self, field_resolver, context: ResolverContext = ResolverContext()):
        expression = self._compile(field_resolver, context, scalar=False).exists()
        return ~expression if self.negated else expression


__all__ = ["Exists", "OuterRef", "Subquery"]
