"""Reference evaluation of S3 Select queries in plain Python.

hadro filters with rugo predicates and Draken masks. This module is the
regression oracle for them: it evaluates the same parsed conditions row by
row with SQL three-valued logic (None is UNKNOWN), and the tests check that
hadro returns exactly the rows it selects.
"""

from __future__ import annotations

import operator
from collections.abc import Callable

from hadro.select.evaluate import coerce, like_regex, resolve_column
from hadro.select.sql import (
    Between,
    BoolOp,
    Column,
    Comparison,
    Condition,
    InList,
    IsNull,
    Like,
    Not,
    Operand,
    SQLError,
    parse,
)

Row = dict

_OPS = {
    "=": operator.eq, "!=": operator.ne, "<": operator.lt,
    "<=": operator.le, ">": operator.gt, ">=": operator.ge,
}  # fmt: skip


def normalise(values: list, target: str) -> list:
    """rugo can return text as bytes; make string columns comparable with str."""
    if target != "str":
        return values
    return [v.decode("utf-8", "replace") if isinstance(v, bytes) else v for v in values]


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
            pattern = like_regex(node.pattern)

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


def select(rows: list[Row], sql: str, kinds: dict[str, str]) -> list[Row]:
    """Apply a query's WHERE, LIMIT and projection to ``rows``."""
    query = parse(sql)
    names = list(rows[0]) if rows else list(kinds)
    rows = [{k: normalise([v], kinds.get(k, ""))[0] for k, v in row.items()} for row in rows]
    if query.where is not None:
        condition = compile_condition(query.where, names, kinds)
        rows = [row for row in rows if condition(row) is True]
    if query.limit is not None:
        rows = rows[: query.limit]
    if query.projection is not None:
        columns = [(resolve_column(names, column), alias) for column, alias in query.projection]
        rows = [{alias: row[source] for source, alias in columns} for row in rows]
    return rows
