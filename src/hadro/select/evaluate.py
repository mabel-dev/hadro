"""Plan and evaluate parsed S3 Select conditions.

Simple ``column <op> literal`` conditions that are ANDed at the top level are
handed to rugo as predicates, which prunes Parquet row groups and filters rows
while reading. Everything else is evaluated here, row by row, over the rows
rugo returns, using SQL three-valued logic (a comparison with NULL is unknown).

Column types are strings as rugo reports them: Parquet footer logical types
(``int64``, ``date32[day]``, ``array<varchar>``) or Draken types
(``DrakenType.INT64``). ``kind`` reduces both to a small set.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from .sql import (
    Between,
    BoolOp,
    Column,
    Comparison,
    Condition,
    InList,
    IsNull,
    Like,
    Literal,
    Not,
    Operand,
    SQLError,
)

Row = dict
Predicate = tuple

_OPS = {
    "=": operator.eq, "!=": operator.ne, "<": operator.lt,
    "<=": operator.le, ">": operator.gt, ">=": operator.ge,
}  # fmt: skip
_FLIPPED = {"=": "=", "!=": "!=", "<": ">", "<=": ">=", ">": "<", ">=": "<="}
_PUSHABLE = {"int", "float", "str", "date", "datetime"}


def kind(type_name: object) -> str:
    """Reduce a rugo/Draken type name to int, float, bool, str, bytes, date,
    datetime, decimal or other (lists, objects and anything uncomparable)."""
    name = str(type_name).lower().removeprefix("drakentype.")
    if any(t in name for t in ("array", "list", "variant", "struct", "map", "interval")):
        return "other"
    if "bool" in name:
        return "bool"
    if "timestamp" in name:
        return "datetime"
    if "date" in name:
        return "date"
    if "decimal" in name:
        return "decimal"
    if "int" in name:
        return "int"
    if "float" in name or "double" in name:
        return "float"
    if "binary" in name:
        return "bytes"
    if "char" in name or "string" in name or "utf8" in name:
        return "str"
    return "other"


def resolve_column(names: list[str], column: Column) -> str:
    if column.name in names:
        return column.name
    if not column.quoted:
        # Unquoted identifiers are case-insensitive in S3 Select.
        matches = [name for name in names if name.lower() == column.name.lower()]
        if len(matches) == 1:
            return matches[0]
    raise SQLError(f"Column {column.name!r} does not exist.")


def coerce(value: object, target: str) -> object:
    """Convert a SQL literal to the Python type of a column of kind ``target``."""
    if value is None:
        return None
    try:
        if target == "int":
            if isinstance(value, bool):
                raise ValueError
            if isinstance(value, (int, float)):
                return value  # compare 3 with 2.5 numerically
            text = str(value)
            return float(text) if any(c in text for c in ".eE") else int(text)
        if target == "float":
            if isinstance(value, bool):
                raise ValueError
            return float(value)
        if target == "decimal":
            return Decimal(str(value))
        if target == "bool":
            if isinstance(value, bool):
                return value
            if str(value).lower() in ("true", "false"):
                return str(value).lower() == "true"
            raise ValueError
        if target == "str":
            if isinstance(value, str):
                return value
            raise ValueError
        if target == "bytes":
            if isinstance(value, str):
                return value.encode("utf-8")
            raise ValueError
        if target == "date":
            return datetime.fromisoformat(str(value)).date()
        if target == "datetime":
            moment = datetime.fromisoformat(str(value))
            return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    except (ValueError, InvalidOperation):
        pass
    raise SQLError(f"Cannot compare {value!r} with a column of type {target}.")


def normalise(values: list, target: str) -> list:
    """rugo can return text as bytes; make string columns comparable with str."""
    if target != "str":
        return values
    return [v.decode("utf-8", "replace") if isinstance(v, bytes) else v for v in values]


def conjuncts(condition: Condition | None) -> list[Condition]:
    if condition is None:
        return []
    if isinstance(condition, BoolOp) and condition.op == "AND":
        return conjuncts(condition.left) + conjuncts(condition.right)
    return [condition]


def plan(
    condition: Condition | None, names: list[str], kinds: dict[str, str], allow_in: bool
) -> tuple[list[Predicate], Condition | None]:
    """Split a WHERE clause into rugo predicates and a residual condition."""
    pushed, residual = [], []
    for part in conjuncts(condition):
        predicate = _predicate(part, names, kinds, allow_in)
        if predicate is None:
            residual.append(part)
        else:
            pushed.append(predicate)
    remainder = None
    for part in residual:
        remainder = part if remainder is None else BoolOp("AND", remainder, part)
    return pushed, remainder


def _predicate(part: Condition, names, kinds, allow_in: bool) -> Predicate | None:
    if isinstance(part, Comparison):
        op, left, right = part.op, part.left, part.right
        if isinstance(left, Literal) and isinstance(right, Column):
            op, left, right = _FLIPPED[op], right, left
        if not (isinstance(left, Column) and isinstance(right, Literal)):
            return None
        if right.value is None:
            return None
        name = resolve_column(names, left)
        target = kinds.get(name)
        if target not in _PUSHABLE:
            return None
        value = coerce(right.value, target)
        if target == "int" and isinstance(value, float):
            return None  # rugo compares like types; leave 3 > 2.5 to Python
        return (name, "==" if op == "=" else op, value)

    if isinstance(part, InList) and allow_in and isinstance(part.operand, Column):
        if any(v.value is None for v in part.values):
            return None
        name = resolve_column(names, part.operand)
        target = kinds.get(name)
        if target not in _PUSHABLE:
            return None
        values = [coerce(v.value, target) for v in part.values]
        if target == "int" and any(isinstance(v, float) for v in values):
            return None
        return (name, "not in" if part.negated else "in", values)
    return None


def columns_used(condition: Condition | None, names: list[str]) -> list[str]:
    found: list[str] = []

    def visit(node) -> None:
        if isinstance(node, Column):
            found.append(resolve_column(names, node))
        elif isinstance(node, (BoolOp,)):
            visit(node.left)
            visit(node.right)
        elif isinstance(node, Not):
            visit(node.operand)
        elif isinstance(node, Comparison):
            visit(node.left)
            visit(node.right)
        elif isinstance(node, (IsNull, InList, Like)):
            visit(node.operand)
        elif isinstance(node, Between):
            visit(node.operand)
            visit(node.low)
            visit(node.high)

    if condition is not None:
        visit(condition)
    return list(dict.fromkeys(found))


def compile_condition(
    condition: Condition, names: list[str], kinds: dict[str, str]
) -> Callable[[Row], bool | None]:
    """Compile a condition to ``row -> True | False | None`` (None is SQL UNKNOWN)."""

    def target_of(operand: Operand | None) -> str | None:
        if isinstance(operand, Column):
            return kinds.get(resolve_column(names, operand))
        return None

    def value_of(operand: Operand, other: Operand | None = None) -> Callable[[Row], object]:
        if isinstance(operand, Column):
            name = resolve_column(names, operand)
            return lambda row: row[name]
        target = target_of(other)
        constant = operand.value if target is None else coerce(operand.value, target)
        return lambda row: constant

    def build(node: Condition) -> Callable[[Row], bool | None]:
        if isinstance(node, BoolOp):
            left, right = build(node.left), build(node.right)
            if node.op == "AND":

                def and_(row):
                    a = left(row)
                    if a is False:
                        return False
                    b = right(row)
                    if b is False:
                        return False
                    return None if a is None or b is None else True

                return and_

            def or_(row):
                a = left(row)
                if a is True:
                    return True
                b = right(row)
                if b is True:
                    return True
                return None if a is None or b is None else False

            return or_

        if isinstance(node, Not):
            inner = build(node.operand)

            def not_(row):
                result = inner(row)
                return None if result is None else not result

            return not_

        if isinstance(node, Comparison):
            left, right = value_of(node.left, node.right), value_of(node.right, node.left)
            compare = _OPS[node.op]

            def comparison(row):
                a, b = left(row), right(row)
                if a is None or b is None:
                    return None
                return compare(a, b)

            return comparison

        if isinstance(node, IsNull):
            value = value_of(node.operand)
            if node.negated:
                return lambda row: value(row) is not None
            return lambda row: value(row) is None

        if isinstance(node, InList):
            value = value_of(node.operand)
            target = target_of(node.operand)
            options = [v.value if target is None else coerce(v.value, target) for v in node.values]
            has_null = any(v is None for v in options)
            options = [v for v in options if v is not None]

            def in_list(row):
                current = value(row)
                if current is None:
                    return None
                if current in options:
                    return not node.negated
                return None if has_null else node.negated

            return in_list

        if isinstance(node, Between):
            value = value_of(node.operand)
            low = value_of(node.low, node.operand)
            high = value_of(node.high, node.operand)

            def between(row):
                current, lo, hi = value(row), low(row), high(row)
                if current is None or lo is None or hi is None:
                    return None
                return (lo <= current <= hi) != node.negated

            return between

        if isinstance(node, Like):
            value = value_of(node.operand)
            pattern = re.compile(_like_to_regex(node.pattern), re.DOTALL)

            def like(row):
                current = value(row)
                if current is None:
                    return None
                if isinstance(current, bytes):
                    current = current.decode("utf-8", "replace")
                if not isinstance(current, str):
                    raise SQLError("LIKE can only be used with string columns.")
                return (pattern.fullmatch(current) is not None) != node.negated

            return like

        raise SQLError(f"Unsupported condition {node!r}.")  # pragma: no cover

    compiled = build(condition)

    def run(row: Row) -> bool | None:
        try:
            return compiled(row)
        except TypeError:
            raise SQLError("The query compares values of incompatible types.") from None

    return run


def _like_to_regex(pattern: str) -> str:
    return "".join(".*" if c == "%" else "." if c == "_" else re.escape(c) for c in pattern)
