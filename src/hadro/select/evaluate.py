"""Plan and evaluate parsed S3 Select conditions on rugo / Draken.

Filtering happens in two places, both native:

1. ``plan`` hands every top-level ANDed condition that the input's rugo reader
   can apply exactly to it as a predicate. For Parquet that prunes row groups
   on footer statistics and filters rows while decoding.
2. ``compile_mask`` turns whatever is left into a Draken boolean vector built
   from the vector compare kernels and ``and``/``or``/``not``, which follow SQL
   three-valued logic, and the morsel is filtered with it.

Column types are strings as rugo reports them: Parquet footer logical types
(``int64``, ``date32[day]``, ``array<varchar>``), JSONL metadata types
(``string``, ``double``) or Draken types (``DrakenType.INT64``). ``kind``
reduces them all to a small set.
"""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from draken import vector_from_sequence
from draken.vectors.bool_vector import BoolVector

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
    SQLError,
)

Predicate = tuple

# Draken compare-kernel op codes.
_OP_CODE = {"=": 0, "!=": 1, ">": 2, ">=": 3, "<": 4, "<=": 5}
# a <op> b  ==  b <flipped op> a
_FLIPPED = {"=": "=", "!=": "!=", "<": ">", "<=": ">=", ">": "<", ">=": "<="}
# NOT (a <op> b)  ==  a <inverse op> b, for filtering (NULL stays excluded either way)
_INVERSE = {"=": "!=", "!=": "=", "<": ">=", "<=": ">", ">": "<=", ">=": "<"}


@dataclass(frozen=True)
class Pushdown:
    """What a rugo reader applies exactly (verified against rugo 0.4.40)."""

    kinds: frozenset
    membership: bool = False  # in / not in
    null_tests: bool = False  # is null / is not null


# rugo's JSONL reader silently returns no rows for in / not in / is null, and its
# CSV reader keeps NULL rows for != and returns empty morsels when a literal's
# type differs from the column's inferred type, so CSV gets no pushdown.
PUSHDOWN = {
    "parquet": Pushdown(
        kinds=frozenset({"int", "float", "str", "date", "datetime", "bool"}),
        membership=True,
        null_tests=True,
    ),
    "json": Pushdown(kinds=frozenset({"int", "float", "str"})),
}


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


def coerce(value: object, target: str | None) -> object:
    """Convert a SQL literal to the Python type of a column of kind ``target``."""
    if value is None or target is None:
        return value
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


def conjuncts(condition: Condition | None) -> list[Condition]:
    if condition is None:
        return []
    if isinstance(condition, BoolOp) and condition.op == "AND":
        return conjuncts(condition.left) + conjuncts(condition.right)
    return [condition]


def columns_used(condition: Condition | None, names: list[str]) -> list[str]:
    found: list[str] = []

    def visit(node) -> None:
        if isinstance(node, Column):
            found.append(resolve_column(names, node))
        elif isinstance(node, (BoolOp, Comparison)):
            visit(node.left)
            visit(node.right)
        elif isinstance(node, (Not, IsNull, InList, Like)):
            visit(node.operand)
        elif isinstance(node, Between):
            visit(node.operand)
            visit(node.low)
            visit(node.high)

    if condition is not None:
        visit(condition)
    return list(dict.fromkeys(found))


# --- 1. rugo predicates --------------------------------------------------------


def plan(
    condition: Condition | None, names: list[str], kinds: dict[str, str], source_format: str
) -> tuple[list[Predicate], Condition | None]:
    """Split a WHERE clause into rugo predicates and a residual condition."""
    capability = PUSHDOWN.get(source_format)
    pushed, residual = [], []
    for part in conjuncts(condition):
        predicates = None if capability is None else _predicates(part, names, kinds, capability)
        if predicates is None:
            residual.append(part)
        else:
            pushed.extend(predicates)
    remainder = None
    for part in residual:
        remainder = part if remainder is None else BoolOp("AND", remainder, part)
    return pushed, remainder


def _pushable_column(operand, names, kinds, capability) -> tuple[str, str] | None:
    if not isinstance(operand, Column):
        return None
    name = resolve_column(names, operand)
    target = kinds.get(name)
    return (name, target) if target in capability.kinds else None


def _predicates(part: Condition, names, kinds, capability: Pushdown) -> list[Predicate] | None:
    negated = False
    if isinstance(part, Not) and isinstance(part.operand, (Comparison, IsNull, InList)):
        negated, part = True, part.operand

    if isinstance(part, Comparison):
        op, left, right = part.op, part.left, part.right
        if isinstance(left, Literal) and isinstance(right, Column):
            op, left, right = _FLIPPED[op], right, left
        column = _pushable_column(left, names, kinds, capability)
        if column is None or not isinstance(right, Literal) or right.value is None:
            return None
        name, target = column
        op = _INVERSE[op] if negated else op
        return [(name, "==" if op == "=" else op, coerce(right.value, target))]

    if isinstance(part, IsNull) and capability.null_tests:
        if not isinstance(part.operand, Column):
            return None
        name = resolve_column(names, part.operand)
        return [(name, "is not null" if part.negated != negated else "is null", None)]

    if isinstance(part, InList) and capability.membership:
        column = _pushable_column(part.operand, names, kinds, capability)
        if column is None or any(v.value is None for v in part.values):
            return None
        name, target = column
        values = [coerce(v.value, target) for v in part.values]
        return [(name, "not in" if part.negated != negated else "in", values)]

    if isinstance(part, Between) and not part.negated:
        column = _pushable_column(part.operand, names, kinds, capability)
        bounds = (part.low, part.high)
        if column is None or not all(
            isinstance(b, Literal) and b.value is not None for b in bounds
        ):
            return None
        name, target = column
        return [
            (name, ">=", coerce(part.low.value, target)),
            (name, "<=", coerce(part.high.value, target)),
        ]
    return None


# --- 2. Draken masks ---------------------------------------------------------


def compile_mask(condition: Condition, morsel) -> BoolVector:
    """Evaluate ``condition`` over ``morsel`` as a Draken boolean vector (NULL = unknown)."""
    names = [n.decode() for n in morsel.column_names]
    kinds = {(k.decode() if isinstance(k, bytes) else k): kind(t) for k, t in morsel.schema.items()}
    rows = morsel.num_rows

    def vector(column: Column):
        name = resolve_column(names, column)
        return name, morsel.column(name.encode()), kinds[name]

    def constant(value: bool | None) -> BoolVector:
        if value is None:
            return BoolVector(vector_from_sequence([None] * rows, dtype="BOOL"))
        return BoolVector.from_constant(bool(value), rows)

    def compare(column: Column, op: str, value: object) -> BoolVector:
        if value is None:
            return constant(None)
        name, vec, target = vector(column)
        scalar = coerce(value, target)
        if isinstance(scalar, str):
            scalar = scalar.encode("utf-8")  # string vectors compare against bytes
        try:
            return vec._compare_scalar(scalar, _OP_CODE[op])
        except (ValueError, TypeError) as exc:
            raise SQLError(f"Cannot filter column {name!r}: {exc}") from None

    def build(node: Condition) -> BoolVector:
        if isinstance(node, BoolOp):
            left, right = build(node.left), build(node.right)
            return left.and_vector(right) if node.op == "AND" else left.or_vector(right)
        if isinstance(node, Not):
            return build(node.operand).not_vector()
        if isinstance(node, Comparison):
            return comparison(node.op, node.left, node.right)
        if isinstance(node, IsNull):
            if isinstance(node.operand, Literal):
                return constant((node.operand.value is None) != node.negated)
            _, vec, _ = vector(node.operand)
            return vec.is_not_null_mask() if node.negated else vec.is_null_mask()
        if isinstance(node, InList):
            return membership(node)
        if isinstance(node, Between):
            return between(node)
        if isinstance(node, Like):
            return like(node)
        raise SQLError(f"Unsupported condition {node!r}.")  # pragma: no cover

    def comparison(op: str, left, right) -> BoolVector:
        if isinstance(left, Literal) and isinstance(right, Literal):
            if left.value is None or right.value is None:
                return constant(None)
            try:
                return constant(_python_compare(op, left.value, right.value))
            except TypeError:
                raise SQLError("The query compares values of incompatible types.") from None
        if isinstance(left, Literal):
            op, left, right = _FLIPPED[op], right, left
        if isinstance(right, Literal):
            return compare(left, op, right.value)
        (lname, lvec, _), (rname, rvec, _) = vector(left), vector(right)
        try:
            return lvec._compare_vector_op(rvec, _OP_CODE[op])
        except (ValueError, TypeError) as exc:
            raise SQLError(f"Cannot compare {lname!r} with {rname!r}: {exc}") from None

    def membership(node: InList) -> BoolVector:
        if isinstance(node.operand, Literal):
            value = node.operand.value
            values = [v.value for v in node.values]
            if value is None:
                result = None
            elif value in [v for v in values if v is not None]:
                result = True
            else:
                result = None if None in values else False
            return constant(result if result is None else result != node.negated)
        # An OR of exact equality masks, as rugo does: Draken's in_list() matches on
        # hashes without verifying values. NULL members make misses unknown.
        mask = None
        for member in node.values:
            member_mask = compare(node.operand, "=", member.value)
            mask = member_mask if mask is None else mask.or_vector(member_mask)
        return mask.not_vector() if node.negated else mask

    def between(node: Between) -> BoolVector:
        bounds = (node.low, node.high)
        if isinstance(node.operand, Column) and all(isinstance(b, Literal) for b in bounds):
            if node.low.value is None or node.high.value is None:
                return constant(None)
            name, vec, target = vector(node.operand)
            low, high = coerce(node.low.value, target), coerce(node.high.value, target)
            if isinstance(low, str):
                low, high = low.encode("utf-8"), high.encode("utf-8")
            try:
                mask = vec.between(low, high)
            except (ValueError, TypeError) as exc:
                raise SQLError(f"Cannot filter column {name!r}: {exc}") from None
        else:
            mask = comparison(">=", node.operand, node.low).and_vector(
                comparison("<=", node.operand, node.high)
            )
        return mask.not_vector() if node.negated else mask

    def like(node: Like) -> BoolVector:
        if isinstance(node.operand, Literal):
            value = node.operand.value
            if value is None:
                return constant(None)
            matched = like_regex(node.pattern).fullmatch(str(value)) is not None
            return constant(matched != node.negated)
        name, vec, target = vector(node.operand)
        if target not in ("str", "bytes"):
            raise SQLError(f"LIKE needs a string column; {name!r} is {target}.")
        pattern = node.pattern
        wildcards = [i for i, c in enumerate(pattern) if c in "%_"]
        if not wildcards:
            mask = compare(node.operand, "=", pattern)
        elif wildcards == [len(pattern) - 1] and pattern.endswith("%"):
            # 'prefix%' is the byte range [prefix, prefix with its last byte + 1).
            prefix = pattern[:-1].encode("utf-8")
            mask = vec._compare_scalar(prefix, _OP_CODE[">="])
            upper = _prefix_upper_bound(prefix)
            if upper is not None:
                mask = mask.and_vector(vec._compare_scalar(upper, _OP_CODE["<"]))
        else:
            regex = like_regex(pattern)
            values = [
                None
                if v is None
                else regex.fullmatch(v.decode("utf-8", "replace") if isinstance(v, bytes) else v)
                is not None
                for v in vec.to_pylist()
            ]
            mask = BoolVector(vector_from_sequence(values, dtype="BOOL"))
        return mask.not_vector() if node.negated else mask

    return build(condition)


_PYTHON_OPS = {
    "=": operator.eq, "!=": operator.ne, "<": operator.lt,
    "<=": operator.le, ">": operator.gt, ">=": operator.ge,
}  # fmt: skip


def _python_compare(op: str, left, right) -> bool:
    return _PYTHON_OPS[op](left, right)


def _prefix_upper_bound(prefix: bytes) -> bytes | None:
    """Smallest byte string greater than every string starting with ``prefix``."""
    trimmed = prefix.rstrip(b"\xff")
    if not trimmed:
        return None
    return trimmed[:-1] + bytes([trimmed[-1] + 1])


def like_regex(pattern: str) -> re.Pattern:
    return re.compile(
        "".join(".*" if c == "%" else "." if c == "_" else re.escape(c) for c in pattern),
        re.DOTALL,
    )
