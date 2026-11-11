import operator
from typing import Any

from sqlalchemy import func
from sqlalchemy.sql.elements import ClauseElement, literal

from async_orm_kit.query.expressions.case import Case
from async_orm_kit.query.expressions.value import Value
from async_orm_kit.query.lookups.resolver import ResolverContext
from async_orm_kit.query.mixins.combinable import CombinableMixin
from async_orm_kit.query.mixins.orderable import OrderableMixin
from async_orm_kit.types import SAFE_SCALAR_TYPES


class F(CombinableMixin, OrderableMixin):
    """Reference to a model field for database-level operations and expressions.

    F objects enable database-side computation by referencing field names that get
    resolved to actual columns at query time. They support arithmetic operations,
    comparisons, and can traverse relationships using double-underscore syntax.
    Operations on F objects create FExpression instances that resolve to SQLAlchemy
    expressions, allowing efficient database-side calculations without loading data
    into Python.

    Common use cases include updating fields based on their current values, computing
    derived fields in annotations, and building complex filter conditions that compare
    fields to each other rather than to literal values.

    Args:
        name: Field name or relationship path using double-underscore syntax.
              Examples: "price", "quantity", "order__customer__name"

    Note:
        F objects are lazy - they don't execute queries until the QuerySet is evaluated.
        Operations create expression trees that resolve to SQL at query compilation time.
        Relationship traversal automatically registers joins with the QueryBuilder,
        ensuring proper JOIN clauses in the generated SQL.
    """

    _name: str
    __slots__ = ("_name",)

    @property
    def name(self) -> str:
        return self._name

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("F expressions are immutable.")

    def __init__(self, name: str) -> None:
        """Initialize F object with field name or relationship path.

        Args:
            name: Field name or relationship path.
             Use double-underscore for relationships: "field_name" or "relation__field_name"

        Example:
            >>> F("price")
            >>> F("order__customer__email")
        """
        if not isinstance(name, str) or not name:
            raise ValueError("F() requires a non-empty field name.")
        object.__setattr__(self, "_name", name)

    def resolve(self, field_resolver, context: ResolverContext = ResolverContext()):
        """Resolve field name to SQLAlchemy column expression.

        Uses FieldResolver to handle relationship traversal and join registration.
        The resolver maintains join state across the entire query, ensuring F objects
        in different parts of the query (filters, annotations, order_by) share the
        same join strategy.

        Args:
            field_resolver: FieldResolver instance for column resolution and join tracking.
            context: ResolverContext for OR context and filter marking propagation.

        Returns:
            SQLAlchemy Column object representing the resolved field.

        Example:
            >>> # Internal usage during query compilation
            >>> f = F("order__customer__name")
            >>> column = f.resolve(field_resolver, context)
            >>> # Returns Customer.name column with joins registered
        """
        resolution = field_resolver.resolve_to_column(key=self.name, context=context)
        return resolution.column

    def __pow__(self, other):
        """Power operation: F("x") ** 2 creates exponentiation expression."""
        return FExpression(self, "pow", other)

    def __rpow__(self, other):
        """Reverse power: 2 ** F("x") creates exponentiation expression."""
        return FExpression(other, "pow", self)

    def __eq__(self, other):
        """Equality comparison: F("x") == value creates comparison expression."""
        return FExpression(self, operator.eq, other)

    def __ne__(self, other):
        """Inequality comparison: F("x") != value creates comparison expression."""
        return FExpression(self, operator.ne, other)

    def __lt__(self, other):
        """Less than: F("x") < value creates comparison expression."""
        return FExpression(self, operator.lt, other)

    def __le__(self, other):
        """Less than or equal: F("x") <= value creates comparison expression."""
        return FExpression(self, operator.le, other)

    def __gt__(self, other):
        """Greater than: F("x") > value creates comparison expression."""
        return FExpression(self, operator.gt, other)

    def __ge__(self, other):
        """Greater than or equal: F("x") >= value creates comparison expression."""
        return FExpression(self, operator.ge, other)

    def __pos__(self):
        """Unary plus: +F("x") returns self unchanged."""
        return self

    def __repr__(self) -> str:
        """Return string representation for debugging."""
        return f"F('{self.name}')"

    def __hash__(self) -> int:
        """Return hash based on field name for use in sets and dicts."""
        return hash(self.name)


class FExpression(CombinableMixin, OrderableMixin):
    """Complex expression tree built from F objects and operations.

    FExpression represents the result of operations on F objects, creating an
    expression tree that resolves to SQLAlchemy expressions at query time. Supports
    arithmetic operations (``+``, ``-``, ``*``, ``/``, ``//``, ``%``, ``**``),
    comparisons (``==``, ``!=``, ``<``, ``<=``, ``>``, ``>=``),
    bitwise operations (``&``, ``|``, ``^``), and unary operations (-, ~, abs).

    Expressions can be nested arbitrarily deep and combined with literals, other F
    objects, Value() wrappers, Case() conditionals, and Aggregate functions. The
    expression tree is evaluated lazily during query compilation, generating efficient
    SQL that performs all computation database-side.

    Args:
        left: Left operand - can be F, FExpression, Value, Case, Aggregate, scalar,
              or None for unary operations.
        op: Operator function from operator module or special string like "pow", "abs".
        right: Right operand - same types as left.

    Note:
        Expressions are immutable - each operation creates a new FExpression instance.
        This enables safe reuse of expression components across multiple queries.

        Resolution is recursive - complex nested expressions resolve from leaves to
        root, with each operand resolving independently before the operation is applied.
    """

    _left: Any
    _operator: Any
    _right: Any
    __slots__ = ("_left", "_operator", "_right")

    @property
    def left(self):
        return self._left

    @property
    def operator(self):
        return self._operator

    @property
    def right(self):
        return self._right

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("FExpression objects are immutable.")

    def __init__(self, left, op, right):
        """Initialize expression with operands and operator.

        Args:
            left: Left operand or None for unary operations.
            op: Operator function or special string ("pow", "abs").
            right: Right operand.
        """
        allowed_operators = {
            operator.add,
            operator.sub,
            operator.mul,
            operator.truediv,
            operator.mod,
            operator.neg,
            operator.eq,
            operator.ne,
            operator.lt,
            operator.le,
            operator.gt,
            operator.ge,
            "pow",
            "abs",
            "floordiv",
            "bit_and",
            "bit_or",
            "bit_not",
        }
        if op not in allowed_operators:
            raise ValueError(f"Unsupported F expression operator: {op!r}")
        object.__setattr__(self, "_left", left)
        object.__setattr__(self, "_operator", op)
        object.__setattr__(self, "_right", right)

    def resolve(
        self,
        field_resolver,
        context: ResolverContext = ResolverContext(),
    ):
        """Resolve expression tree to SQLAlchemy expression.

        Recursively resolves operands then applies the operator.
        Handles special function-based operations (abs, pow), unary operations (left=None), and
        standard binary operations.

        Args:
            field_resolver: FieldResolver instance for column resolution and join tracking.
            context: ResolverContext for OR context and filter marking propagation.

        Returns:
            SQLAlchemy expression representing the resolved operation.

        Example:
            >>> # Internal usage during query compilation
            >>> expr = F("price") * F("quantity")
            >>> sql_expr = expr.resolve(field_resolver, context)
            >>> # Returns: products.price * products.quantity
        """
        left_val = self._resolve_operand(self.left, field_resolver, context)
        right_val = self._resolve_operand(self.right, field_resolver, context)

        # Handle special function-based operations
        if self.operator == "abs":
            return func.abs(right_val)
        if self.operator == "floordiv":
            return func.floor(left_val / right_val)
        if self.operator == "bit_and":
            return left_val.bitwise_and(right_val)
        if self.operator == "bit_or":
            return left_val.bitwise_or(right_val)
        if self.operator == "bit_not":
            return right_val.bitwise_not()
        elif self.operator == "pow":
            return func.power(left_val, right_val)

        # Handle unary operations
        if self.left is None:
            return self.operator(right_val)

        # Handle binary operations
        return self.operator(left_val, right_val)

    def _resolve_operand(
        self, operand, field_resolver, context: ResolverContext = ResolverContext()
    ):
        """Resolve operand to SQLAlchemy expression or literal.

        Handles multiple operand types with priority-based resolution:
        1. None - Returns None for unary operations
        2. F/FExpression - Resolves to column/expression
        3. Value/Case - Resolves to literal/conditional
        4. Aggregate - Resolves to aggregate function
        5. ClauseElement - SQLAlchemy expression passed through
        6. Safe scalars - Wrapped in literal()
        7. Other types - Raises TypeError

        Args:
            operand: Value to resolve.
            field_resolver: FieldResolver for column resolution.
            context: ResolverContext for propagation.

        Returns:
            SQLAlchemy expression or literal value.

        Raises:
            TypeError: If operand type is unsupported.
        """
        from async_orm_kit.query.aggregations.base import Aggregate

        if operand is None:
            return None

        if isinstance(operand, (F, FExpression)):
            return operand.resolve(field_resolver, context)

        if isinstance(operand, (Value, Case)):
            return operand.resolve(field_resolver, context)

        if isinstance(operand, Aggregate):
            return operand.resolve(field_resolver=field_resolver, context=context)

        if isinstance(operand, CombinableMixin) and hasattr(operand, "resolve"):
            return operand.resolve(field_resolver=field_resolver, context=context)

        if isinstance(operand, ClauseElement):
            return operand

        if isinstance(operand, SAFE_SCALAR_TYPES):
            return literal(operand)

        raise TypeError(
            f"Unsupported operand type in F expression: {type(operand).__name__!r}. "
            f"Use F(), Value(), Case(), or a scalar (int, float, str, etc.)."
        )

    # ── Operations NOT provided by CombinableMixin ──

    def __pow__(self, other):
        """Power operation: expr ** 2 creates nested exponentiation."""
        return FExpression(self, "pow", other)

    def __rpow__(self, other):
        """Reverse power: 2 ** expr creates nested exponentiation."""
        return FExpression(other, "pow", self)

    def __eq__(self, other):
        """Equality comparison: expr == value creates comparison."""
        return FExpression(self, operator.eq, other)

    def __ne__(self, other):
        """Inequality comparison: expr != value creates comparison."""
        return FExpression(self, operator.ne, other)

    def __lt__(self, other):
        """Less than: expr < value creates comparison."""
        return FExpression(self, operator.lt, other)

    def __le__(self, other):
        """Less than or equal: expr <= value creates comparison."""
        return FExpression(self, operator.le, other)

    def __gt__(self, other):
        """Greater than: expr > value creates comparison."""
        return FExpression(self, operator.gt, other)

    def __ge__(self, other):
        """Greater than or equal: expr >= value creates comparison."""
        return FExpression(self, operator.ge, other)

    def __hash__(self) -> int:
        """Return hash based on operand identities for use in sets and dicts."""
        return hash((id(self.left), id(self.operator), id(self.right)))

    def __repr__(self) -> str:
        """Return string representation showing expression tree structure."""
        op_name = (
            self.operator.__name__
            if hasattr(self.operator, "__name__")
            else str(self.operator)
        )
        return f"FExpression({self.left!r}, {op_name}, {self.right!r})"
