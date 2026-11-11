from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, or_

from async_orm_kit.query.lookups.resolver import ResolverContext
from async_orm_kit.types import SAFE_SCALAR_TYPES

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import ColumnElement

    from async_orm_kit.query.lookups.resolver import FieldResolver


class Q:
    """Q object for building complex query conditions with boolean logic.

    Enables composable query filters using Python operators (``&``, ``|``, ``~``) to create
    complex WHERE clauses. Supports nested conditions, field lookups (e.g., age__gt),
    and relationship traversal (e.g., books__author__name). Resolves to SQLAlchemy
    boolean expressions that can be combined with QuerySet.filter() and .exclude().

    The Q object uses a tree structure where leaf nodes are field conditions and
    internal nodes are boolean operators (AND/OR). Negation (~) inverts the entire
    subtree. This design mirrors Django's Q objects for familiar syntax while
    integrating with SQLAlchemy's expression system.

    Attributes:
        condition_nodes: Immutable tuple of nested Q nodes or leaf condition tuples in
            format ("leaf", field_key, value).
        connector: Boolean operator joining children - either "AND" or "OR".
        negated: Whether this Q subtree is negated (inverted with ~ operator).
        AND: Class constant "AND" for internal connector representation.
        OR: Class constant "OR" for internal connector representation.

    Example:
        >>> from your_app.models import DBUser
        >>>
        >>> # Simple AND condition
        >>> q = Q(status="active") & Q(age__gte=18)
        >>> users = DBUser.objects.filter(q)
        >>>
        >>> # OR condition
        >>> q = Q(is_admin=True) | Q(is_moderator=True)
        >>> privileged = DBUser.objects.filter(q)
        >>>
        >>> # Negation
        >>> q = ~Q(is_deleted=True)
        >>> active = DBUser.objects.filter(q)
        >>>
        >>> # Complex nested conditions
        >>> q = (Q(status="active") & Q(age__gte=18)) | Q(is_verified=True)
        >>> eligible = DBUser.objects.filter(q)
        >>>
        >>> # Relationship traversal
        >>> q = Q(posts__status="published") & Q(posts__views__gte=1000)
        >>> popular_authors = DBUser.objects.filter(q)
        >>>
        >>> # Multiple conditions in constructor
        >>> q = Q(status="active", age__gte=18, country="US")
        >>> # Equivalent to: Q(status="active") & Q(age__gte=18) & Q(country="US")

    Note:
        Performance: Q object construction is O(n) where n is number of conditions.
        Resolution to SQLAlchemy expressions is O(n) for tree traversal. The actual
        query performance depends on generated SQL and database indexes.

        Q objects are immutable - operators (``&``, ``|``, ``~``) create new Q instances rather
        than modifying existing ones. This enables safe reuse of Q objects across
        multiple queries.
    """

    AND = "AND"
    OR = "OR"
    _children: tuple
    _connector: str
    _negated: bool
    __slots__ = ("_children", "_connector", "_negated")

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("Q objects are immutable.")

    @property
    def children(self) -> tuple:
        """Return a read-only snapshot of nested nodes and leaf conditions."""
        return tuple(
            child
            if isinstance(child, Q)
            else ("leaf", child[1], _snapshot_value(child[2]))
            for child in self._children
        )

    @property
    def connector(self) -> str:
        return self._connector

    @property
    def negated(self) -> bool:
        return self._negated

    @classmethod
    def _from_parts(cls, children: tuple, connector: str, negated: bool) -> Q:
        instance = object.__new__(cls)
        object.__setattr__(instance, "_children", tuple(children))
        object.__setattr__(instance, "_connector", connector)
        object.__setattr__(instance, "_negated", negated)
        return instance

    def __init__(self, *args, **kwargs):
        """Initialize Q object with nested Q objects and/or field conditions.

        Accepts nested Q objects as positional arguments and field lookups as
        keyword arguments. Keyword arguments are converted to leaf condition tuples.
        All conditions are combined with AND by default (can be changed with | operator).

        Args:
            *args: Nested Q objects to include as children. Must be Q instances.
            **kwargs: Field lookups in format field__lookup=value (e.g., age__gte=18).
                     Standard field names without lookups default to exact match.

        Raises:
            TypeError: If positional arguments contain non-Q objects.

        Example:
            >>> # Keyword arguments only
            >>> q = Q(status="active", age__gte=18)
            >>>
            >>> # Nested Q objects
            >>> q1 = Q(status="active")
            >>> q2 = Q(age__gte=18)
            >>> q = Q(q1, q2)  # Combines with AND
            >>>
            >>> # Mixed nested and keyword
            >>> q = Q(Q(status="active"), age__gte=18)
            >>>
            >>> # Invalid: non-Q positional argument
            >>> q = Q("invalid")  # Raises TypeError
        """
        children: list[Q | tuple[str, str, Any]] = []

        for arg in args:
            if isinstance(arg, Q):
                children.append(arg)
            else:
                raise TypeError(
                    f"Q() positional arguments must be Q objects, "
                    f"not {type(arg).__name__}"
                )

        children.extend(
            ("leaf", key, _snapshot_value(value)) for key, value in kwargs.items()
        )
        object.__setattr__(self, "_children", tuple(children))
        object.__setattr__(self, "_connector", self.AND)
        object.__setattr__(self, "_negated", False)

    def __and__(self, other: Q) -> Q:
        """Combine two Q objects with AND logic using & operator.

        Creates a new Q object where both conditions must be true. Optimizes by
        flattening when both Q objects use AND connector and neither is negated,
        avoiding unnecessary nesting in the expression tree.

        Args:
            other: Q object to combine with AND logic.

        Returns:
            New Q object representing (self AND other).

        Raises:
            TypeError: If other is not a Q object.

        Example:
            >>> q1 = Q(status="active")
            >>> q2 = Q(age__gte=18)
            >>> combined = q1 & q2
            >>> # SQL: WHERE status = 'active' AND age >= 18
            >>>
            >>> # Flattening optimization
            >>> q3 = Q(country="US")
            >>> all_conditions = q1 & q2 & q3
            >>> # Creates flat AND with 3 children, not nested structure
        Note:
            Performance: O(1) for simple combination, O(n+m) when flattening wheren and m are children counts.
             Returns other unchanged if self is empty.
        """
        if not isinstance(other, Q):
            raise TypeError(f"Cannot combine Q with {type(other)}")
        return self._combine(other, self.AND)

    def __or__(self, other: Q) -> Q:
        """Combine two Q objects with OR logic using | operator.

        Creates a new Q object where either condition can be true. Optimizes by
        flattening when both Q objects use OR connector and neither is negated,
        avoiding unnecessary nesting in the expression tree.

        Args:
            other: Q object to combine with OR logic.

        Returns:
            New Q object representing (self OR other).

        Raises:
            TypeError: If other is not a Q object.

        Example:
            >>> q1 = Q(is_admin=True)
            >>> q2 = Q(is_moderator=True)
            >>> combined = q1 | q2
            >>> # SQL: WHERE is_admin = true OR is_moderator = true
            >>>
            >>> # Complex combination
            >>> q3 = Q(is_owner=True)
            >>> privileged = q1 | q2 | q3
            >>> # Creates flat OR with 3 children>>>
            >>> # Mixed operators
            >>> active_admins = (Q(status="active") & Q(is_admin=True)) | Q(is_superuser=True)
            >>> # SQL: WHERE (status = 'active' AND is_admin = true) OR is_superuser = true

        Note:
            Performance: O(1) for simple combination, O(n+m) when flattening where
            n and m are children counts. OR conditions may require outer joins for
            relationships to avoid filtering out NULL values.
        """
        if not isinstance(other, Q):
            raise TypeError(f"Cannot combine Q with {type(other)}")
        return self._combine(other, self.OR)

    def __invert__(self) -> Q:
        """Negate Q object using ~ operator to invert boolean logic.

        Creates a new Q object with negated flag flipped. When resolved to SQL,
        negation wraps the entire condition in NOT(...). Double negation cancels
        out (~~Q is equivalent to Q).

        Returns:
            New Q object with inverted negation flag.

        Example:
            >>> # Simple negation
            >>> q = ~Q(is_deleted=True)
            >>> # SQL: WHERE NOT (is_deleted = true)
            >>>
            >>> # Negating complex conditions
            >>> q = ~(Q(status="inactive") | Q(is_banned=True))
            >>> # SQL: WHERE NOT (status = 'inactive' OR is_banned = true)
            >>> # Equivalent to: WHERE status != 'inactive' AND is_banned = false>>>
            >>> # Double negation cancels
            >>> q = ~~Q(status="active")
            >>> # Equivalent to: Q(status="active")
            >>>
            >>> # Combining with other operators
            >>> q = Q(age__gte=18) & ~Q(country="US")
            >>> # SQL: WHERE age >= 18 AND NOT (country = 'US')
        Note:
            Performance: O(1) - creates shallow copy with flipped negation flag.
            Negation is applied during resolve() by wrapping SQLAlchemy expression
            in not_() function.
        """
        return self._from_parts(
            children=self.children,
            connector=self.connector,
            negated=not self.negated,
        )

    def _combine(self, other: Q, connector: str) -> Q:
        """Combine two Q objects with specified connector, optimizing tree structure.

        Internal method implementing AND/OR combination logic. Performs flattening
        optimization when both Q objects use the same connector and neither is negated,
        reducing tree depth and improving resolution performance. Returns unchanged
        Q object if one side is empty.

        Args:
            other: Q object to combine with.
            connector: Either "AND" or "OR" string constant.

        Returns:
            New Q object representing combined condition with optimal structure.

        Example:
            >>> # Internal usage by __and__ and __or__
            >>> q1 = Q(a=1)
            >>> q2 = Q(b=2)
            >>> result = q1._combine(q2, Q.AND)
            >>>
            >>> # Flattening optimization
            >>> q1 = Q(a=1) & Q(b=2)  # AND connector, not negated
            >>> q2 = Q(c=3) & Q(d=4)  # AND connector, not negated
            >>> result = q1._combine(q2, Q.AND)
            >>> # result.children = [("leaf", "a", 1), ("leaf", "b", 2),
            >>> #                     ("leaf", "c", 3), ("leaf", "d", 4)]
            >>> # Instead of nested structure: [q1, q2]

        Note:
            Performance: O(1) when creating nested structure, O(n+m) when flattening
            where n and m are children counts. Flattening reduces tree depth from
            O(n) to O(1) for chains of same-connector operations.
        """
        if not self.children:
            return other
        if not other.children:
            return self

        # If both have same connector and neither is negated, flatten
        if (
            self.connector == connector
            and other.connector == connector
            and not self.negated
            and not other.negated
        ):
            return self._from_parts(
                children=self.children + other.children,
                connector=connector,
                negated=False,
            )

        return self._from_parts(
            children=(self, other),
            connector=connector,
            negated=False,
        )

    # ------------------------------------------------------------------ #
    #  Resolution                                                         #
    # ------------------------------------------------------------------ #

    def resolve(
        self,
        field_resolver: FieldResolver,
        context: ResolverContext = ResolverContext(),
    ) -> ColumnElement[bool] | None:
        """Resolve Q object tree into SQLAlchemy boolean expression for WHERE clause.

        Recursively walks the Q tree structure, resolving leaf conditions through
        FilterParser and combining nested Q objects with appropriate boolean operators.
        Handles negation, OR context propagation for join strategy, and filter marking
        for INNER join promotion. Returns None for empty Q objects.

        The resolution process:

        1. Determines effective context (negation, OR branch, filter marking)
        2. Iterates through children, resolving nested Q objects recursively
        3. Parses leaf conditions using FilterParser with field lookups
        4. Combines resolved conditions with AND/OR based on connector
        5. Applies negation wrapper if Q object is negated

        Args:
            field_resolver: FieldResolver instance for column resolution and join
                          tracking. Shared across entire query to maintain join state.
            context: ResolverContext tracking negation state, OR context for join
                    strategy, and filter marking for INNER join promotion. Defaults
                    to empty context (not negated, not in OR, mark as filter).

        Returns:
            SQLAlchemy BooleanClauseList (``and_``/``or_``/``not_`` expression) representing
            the combined conditions, or None if Q object has no children.

        Raises:
            FilterError: If leaf condition references unknown field, uses invalid
                        lookup, or has malformed field__lookup syntax.
            TypeError: If children contain invalid types (not Q or leaf tuple).

        Example:
            >>> from async_orm_kit.models import User
            >>> from async_orm_kit.query.lookups.resolver import FieldResolver
            >>>
            >>> # Simple resolution
            >>> q = Q(status="active") & Q(age__gte=18)
            >>> resolver = FieldResolver(User)
            >>> condition = q.resolve(resolver)
            >>> # Returns: and_(User.status == 'active', User.age >= 18)
            >>>
            >>> # OR with relationship traversal
            >>> q = Q(posts__status="published") | Q(is_verified=True)
            >>> condition = q.resolve(resolver)
            >>> # Automatically creates LEFT JOIN for posts due to OR context>>>
            >>> # Negation
            >>> q = ~Q(is_deleted=True)
            >>> condition = q.resolve(resolver)
            >>> # Returns: not_(User.is_deleted == True)>>>
            >>> # Complex nested conditions
            >>> q = (Q(status="active") & Q(age__gte=18)) | Q(is_admin=True)
            >>> condition = q.resolve(resolver)
            >>> # Returns: or_(and_(User.status == 'active', User.age >= 18),
            >>> #              User.is_admin == True)
            >>>
            >>> # Empty Q object
            >>> q = Q()
            >>> condition = q.resolve(resolver)
            >>> # Returns: None
        Note:
            Performance: O(n) where n is total number of nodes in Q tree (nested Q
            objects + leaf conditions). Each leaf condition triggers field resolution
            which may create joins - use select_related() to optimize relationship
            queries. OR conditions require outer joins which are slower than inner joins.

            Context propagation: OR context flows down to children so joins inside OR
            branches use LEFT JOIN strategy. Negation context affects join marking.
            Filter marking promotes joins to INNER for better query optimization.
        """
        from async_orm_kit.query.lookups.parser import FilterParser

        if not self.children:
            return None

        if self.negated:
            from async_orm_kit.query.lookups.correlated import resolve_q

            return resolve_q(self, field_resolver, context)

        is_or = self.connector == self.OR
        effective_context = context
        child_context = (
            effective_context.with_or()
            if self.connector == self.OR
            else effective_context
        )

        parser = FilterParser(field_resolver)
        conditions = []

        for child in self.children:
            if isinstance(child, Q):
                cond = child.resolve(
                    field_resolver=field_resolver, context=child_context
                )
                if cond is not None:
                    conditions.append(cond)

            else:
                _, field_key, value = child
                parsed = parser.parse({field_key: value}, context=child_context)
                conditions.extend(parsed)

        if not conditions:
            return None

        result = or_(*conditions) if is_or else and_(*conditions)
        return result

    def __repr__(self) -> str:
        """Return string representation showing connector, negation, and child count.

        Returns:
            String in format "<Q: [NOT] (connector N children)>" for debugging.

        Example:
            >>> q = Q(status="active") & Q(age__gte=18)
            >>> repr(q)
            '<Q: AND 2 children>'
            >>>
            >>> q = ~(Q(a=1) | Q(b=2))
            >>> repr(q)
            '<Q: NOT (OR 2 children)>'
        """
        if self.negated:
            return f"<Q: NOT ({self.connector} {len(self.children)} children)>"
        return f"<Q: {self.connector} {len(self.children)} children>"

    def __bool__(self) -> bool:
        """Return True if Q object has children, False if empty.

        Returns:
            Boolean indicating whether Q object contains any conditions.

        Example:
            >>> q = Q(status="active")
            >>> bool(q)  # True
            >>>
            >>> q = Q()
            >>> bool(q)  # False
        """
        return bool(self.children)

    def __eq__(self, other: object) -> bool:
        """Compare immutable Q trees without invoking overloaded SQL operators."""
        return (
            isinstance(other, Q)
            and self.connector == other.connector
            and self.negated == other.negated
            and _freeze_value(self.children) == _freeze_value(other.children)
        )

    def __hash__(self) -> int:
        """Hash the immutable tree, including collection-valued lookup operands."""
        return hash(
            (
                "Q",
                self.connector,
                self.negated,
                _freeze_value(self.children),
            )
        )


def _snapshot_value(value: Any, active: set[int] | None = None) -> Any:
    """Copy mutable lookup containers so later caller mutations cannot alter Q."""
    if active is None:
        active = set()
    if isinstance(value, list):
        identity = id(value)
        if identity in active:
            raise ValueError("Cyclic containers are not supported in Q lookup values.")
        active.add(identity)
        try:
            return [_snapshot_value(item, active) for item in value]
        finally:
            active.remove(identity)
    if isinstance(value, tuple):
        identity = id(value)
        if identity in active:
            raise ValueError("Cyclic containers are not supported in Q lookup values.")
        active.add(identity)
        try:
            return tuple(_snapshot_value(item, active) for item in value)
        finally:
            active.remove(identity)
    if isinstance(value, dict):
        identity = id(value)
        if identity in active:
            raise ValueError("Cyclic containers are not supported in Q lookup values.")
        active.add(identity)
        try:
            return {
                _snapshot_value(key, active): _snapshot_value(item, active)
                for key, item in value.items()
            }
        finally:
            active.remove(identity)
    if isinstance(value, set):
        return {_snapshot_value(item, active) for item in value}
    if isinstance(value, frozenset):
        return frozenset(_snapshot_value(item, active) for item in value)
    return value


def _freeze_value(value: Any, active: set[int] | None = None) -> Any:
    """Build a hashable structural representation without trusting user equality.

    Lookup values may contain lists or mappings (for example ``field__in=[...]``).
    SQL expression objects also overload equality, so unknown objects are compared
    by identity rather than by invoking their SQL-producing ``__eq__`` methods.
    """
    if active is None:
        active = set()

    if isinstance(value, SAFE_SCALAR_TYPES):
        return (type(value).__qualname__, value)

    if isinstance(value, Q):
        return (
            "Q",
            value.connector,
            value.negated,
            _freeze_value(value.children, active),
        )

    if isinstance(value, (tuple, list)):
        identity = id(value)
        if identity in active:
            return ("cycle", type(value).__qualname__, identity)
        active.add(identity)
        try:
            return (
                type(value).__qualname__,
                tuple(_freeze_value(item, active) for item in value),
            )
        finally:
            active.remove(identity)

    if isinstance(value, dict):
        identity = id(value)
        if identity in active:
            return ("cycle", "dict", identity)
        active.add(identity)
        try:
            items = [
                (_freeze_value(key, active), _freeze_value(item, active))
                for key, item in value.items()
            ]
            return ("dict", tuple(sorted(items, key=repr)))
        finally:
            active.remove(identity)

    if isinstance(value, (set, frozenset)):
        # Supported set members are hashable; container self-cycles cannot be
        # constructed through set/frozenset membership.
        return (
            type(value).__qualname__,
            tuple(sorted((_freeze_value(item, active) for item in value), key=repr)),
        )

    return (type(value).__module__, type(value).__qualname__, id(value))
