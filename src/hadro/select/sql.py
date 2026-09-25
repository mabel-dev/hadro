"""A small parser for the subset of S3 Select SQL that hadro supports.

    SELECT <* | column [[AS] alias], ...>
    FROM S3Object [[AS] alias]
    [WHERE <condition>]
    [LIMIT <n>]

Conditions support comparisons (= != <> < <= > >=), IS [NOT] NULL,
[NOT] IN (...), [NOT] BETWEEN ... AND ..., [NOT] LIKE, combined with
AND / OR / NOT and parentheses. Conditions compile to a pyarrow compute
expression so filtering is pushed into the Parquet reader.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pyarrow as pa
import pyarrow.compute as pc


class SQLError(ValueError):
    """The expression is invalid or uses unsupported SQL."""


_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<string>'(?:[^']|'')*')
  | (?P<quoted>"(?:[^"]|"")*")
  | (?P<number>-?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?|-?\.\d+(?:[eE][-+]?\d+)?)
  | (?P<word>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op><=|>=|<>|!=|=|<|>)
  | (?P<punct>[(),.*;\[\]])
    """,
    re.VERBOSE,
)

_KEYWORDS = {
    "SELECT", "FROM", "WHERE", "LIMIT", "AS", "AND", "OR", "NOT",
    "IS", "NULL", "IN", "BETWEEN", "LIKE", "TRUE", "FALSE",
}  # fmt: skip


@dataclass(frozen=True)
class Token:
    kind: str  # keyword, ident, string, number, op, punct, end
    value: str
    position: int


def tokenize(sql: str) -> list[Token]:
    tokens = []
    position = 0
    while position < len(sql):
        match = _TOKEN.match(sql, position)
        if match is None:
            raise SQLError(f"Unexpected character {sql[position]!r} at position {position}.")
        kind = match.lastgroup
        text = match.group()
        if kind == "string":
            tokens.append(Token("string", text[1:-1].replace("''", "'"), position))
        elif kind == "quoted":
            tokens.append(Token("quoted", text[1:-1].replace('""', '"'), position))
        elif kind == "word":
            if text.upper() in _KEYWORDS:
                tokens.append(Token("keyword", text.upper(), position))
            else:
                tokens.append(Token("ident", text, position))
        elif kind != "ws":
            tokens.append(Token(kind, text, position))
        position = match.end()
    tokens.append(Token("end", "", position))
    return tokens


# --- AST --------------------------------------------------------------------


@dataclass(frozen=True)
class Column:
    name: str
    quoted: bool = False  # quoted identifiers match case-sensitively


@dataclass(frozen=True)
class Literal:
    value: object


Operand = Column | Literal


@dataclass(frozen=True)
class Comparison:
    op: str
    left: Operand
    right: Operand


@dataclass(frozen=True)
class IsNull:
    operand: Operand
    negated: bool


@dataclass(frozen=True)
class InList:
    operand: Operand
    values: tuple[Literal, ...]
    negated: bool


@dataclass(frozen=True)
class Between:
    operand: Operand
    low: Operand
    high: Operand
    negated: bool


@dataclass(frozen=True)
class Like:
    operand: Operand
    pattern: str
    negated: bool


@dataclass(frozen=True)
class BoolOp:
    op: str  # AND / OR
    left: Condition
    right: Condition


@dataclass(frozen=True)
class Not:
    operand: Condition


Condition = Comparison | IsNull | InList | Between | Like | BoolOp | Not


@dataclass
class Query:
    # None means SELECT *; otherwise (column, output name) pairs.
    projection: list[tuple[Column, str]] | None = None
    where: Condition | None = None
    limit: int | None = None
    columns_used: list[Column] = field(default_factory=list)


# --- Parser -----------------------------------------------------------------


class _Parser:
    def __init__(self, sql: str):
        self.tokens = tokenize(sql)
        self.index = 0
        self.alias: str | None = None
        # Column references seen before the FROM alias is known are fixed up later.
        self.pending_refs: list[tuple[str | None, Column]] = []

    @property
    def token(self) -> Token:
        return self.tokens[self.index]

    def advance(self) -> Token:
        token = self.token
        self.index += 1
        return token

    def accept(self, kind: str, value: str | None = None) -> Token | None:
        token = self.token
        if token.kind == kind and (value is None or token.value == value):
            self.index += 1
            return token
        return None

    def expect(self, kind: str, value: str | None = None) -> Token:
        token = self.accept(kind, value)
        if token is None:
            found = self.token.value or "end of expression"
            wanted = value or kind
            raise SQLError(
                f"Expected {wanted} but found {found!r} at position {self.token.position}."
            )
        return token

    def parse(self) -> Query:
        query = Query()
        self.expect("keyword", "SELECT")
        projection = self.parse_projection()
        self.expect("keyword", "FROM")
        source = self.accept("ident") or self.accept("quoted")
        if source is None or source.value.lower() != "s3object":
            raise SQLError("The FROM clause must be S3Object.")
        if self.accept("punct", "["):  # S3Object[*] path syntax is for JSON input
            raise SQLError("Path expressions in FROM are not supported for Parquet input.")
        if self.accept("keyword", "AS"):
            self.alias = self.expect("ident").value
        elif self.token.kind == "ident":
            self.alias = self.advance().value

        if self.accept("keyword", "WHERE"):
            query.where = self.parse_or()
        if self.accept("keyword", "LIMIT"):
            limit = self.expect("number").value
            if not limit.isdigit():
                raise SQLError("LIMIT must be a non-negative integer.")
            query.limit = int(limit)
        self.accept("punct", ";")
        if self.token.kind != "end":
            raise SQLError(f"Unexpected {self.token.value!r} at position {self.token.position}.")

        query.columns_used = [self.resolve_alias(prefix, col) for prefix, col in self.pending_refs]
        if projection is not None:
            columns = iter(query.columns_used)
            # Projection columns were recorded first, in order.
            query.projection = [(next(columns), name) for _, name in projection]
        return query

    def resolve_alias(self, prefix: str | None, column: Column) -> Column:
        if prefix is not None and (self.alias is None or prefix.lower() != self.alias.lower()):
            raise SQLError(f"Unknown table alias {prefix!r}.")
        return column

    def parse_projection(self) -> list[tuple[Column, str]] | None:
        if self.accept("punct", "*"):
            return None
        items = []
        while True:
            if self.token.kind == "ident" and self.tokens[self.index + 1].value == "(":
                raise SQLError(f"Function {self.token.value!r} is not supported.")
            column = self.parse_column_ref()
            name = column.name
            if self.accept("keyword", "AS") or self.token.kind in ("ident", "quoted"):
                name = self.parse_identifier()
            items.append((column, name))
            if not self.accept("punct", ","):
                return items

    def parse_identifier(self) -> str:
        token = self.accept("ident") or self.accept("quoted")
        if token is None:
            raise SQLError(f"Expected an identifier at position {self.token.position}.")
        return token.value

    def parse_column_ref(self) -> Column:
        token = self.accept("ident") or self.accept("quoted")
        if token is None:
            found = self.token.value or "end of expression"
            raise SQLError(f"Expected a column name but found {found!r}.")
        prefix = None
        if self.accept("punct", "."):
            if self.accept("punct", "*"):
                raise SQLError("alias.* is not supported; use *.")
            prefix = token.value
            token = self.accept("ident") or self.accept("quoted")
            if token is None:
                raise SQLError("Expected a column name after '.'.")
        column = Column(token.value, quoted=token.kind == "quoted")
        self.pending_refs.append((prefix, column))
        return column

    def parse_or(self) -> Condition:
        left = self.parse_and()
        while self.accept("keyword", "OR"):
            left = BoolOp("OR", left, self.parse_and())
        return left

    def parse_and(self) -> Condition:
        left = self.parse_not()
        while self.accept("keyword", "AND"):
            left = BoolOp("AND", left, self.parse_not())
        return left

    def parse_not(self) -> Condition:
        if self.accept("keyword", "NOT"):
            return Not(self.parse_not())
        return self.parse_predicate()

    def parse_predicate(self) -> Condition:
        if self.accept("punct", "("):
            condition = self.parse_or()
            self.expect("punct", ")")
            return condition

        operand = self.parse_operand()
        if self.accept("keyword", "IS"):
            negated = bool(self.accept("keyword", "NOT"))
            self.expect("keyword", "NULL")
            return IsNull(operand, negated)

        negated = bool(self.accept("keyword", "NOT"))
        if self.accept("keyword", "IN"):
            self.expect("punct", "(")
            values = [self.parse_literal()]
            while self.accept("punct", ","):
                values.append(self.parse_literal())
            self.expect("punct", ")")
            return InList(operand, tuple(values), negated)
        if self.accept("keyword", "BETWEEN"):
            low = self.parse_operand()
            self.expect("keyword", "AND")
            return Between(operand, low, self.parse_operand(), negated)
        if self.accept("keyword", "LIKE"):
            return Like(operand, self.expect("string").value, negated)
        if negated:
            raise SQLError("Expected IN, BETWEEN or LIKE after NOT.")

        op = self.expect("op").value
        return Comparison("!=" if op == "<>" else op, operand, self.parse_operand())

    def parse_operand(self) -> Operand:
        if self.token.kind in ("ident", "quoted"):
            if self.tokens[self.index + 1].value == "(":
                raise SQLError(f"Function {self.token.value!r} is not supported.")
            return self.parse_column_ref()
        return self.parse_literal()

    def parse_literal(self) -> Literal:
        token = self.advance()
        if token.kind == "string":
            return Literal(token.value)
        if token.kind == "number":
            text = token.value
            is_float = any(c in text for c in ".eE")
            return Literal(float(text) if is_float else int(text))
        if token.kind == "keyword" and token.value in ("TRUE", "FALSE"):
            return Literal(token.value == "TRUE")
        if token.kind == "keyword" and token.value == "NULL":
            return Literal(None)
        raise SQLError(f"Expected a value but found {token.value or 'end of expression'!r}.")


def parse(sql: str) -> Query:
    return _Parser(sql).parse()


# --- Compilation against a schema --------------------------------------------


def resolve_column(schema: pa.Schema, column: Column) -> str:
    if column.name in schema.names:
        return column.name
    if not column.quoted:
        # Unquoted identifiers are case-insensitive in S3 Select.
        matches = [name for name in schema.names if name.lower() == column.name.lower()]
        if len(matches) == 1:
            return matches[0]
    raise SQLError(f"Column {column.name!r} does not exist.")


def _scalar(value: object, target: pa.DataType | None) -> pa.Scalar:
    if target is None or value is None:
        return pa.scalar(value) if target is None else pa.scalar(None, type=target)
    try:
        if isinstance(value, float) and pa.types.is_integer(target):
            return pa.scalar(value)  # let Arrow compare 3 against 2.5
        return pa.scalar(value).cast(target)
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError, pa.ArrowTypeError):
        raise SQLError(f"Cannot compare {value!r} with a column of type {target}.") from None


class _Compiler:
    def __init__(self, schema: pa.Schema):
        self.schema = schema

    def field_type(self, operand: Operand) -> pa.DataType | None:
        if isinstance(operand, Column):
            return self.schema.field(resolve_column(self.schema, operand)).type
        return None

    def operand(self, operand: Operand, other: Operand | None = None) -> pc.Expression:
        if isinstance(operand, Column):
            return pc.field(resolve_column(self.schema, operand))
        target = self.field_type(other) if other is not None else None
        return pc.scalar(_scalar(operand.value, target))

    def compile(self, node: Condition) -> pc.Expression:
        if isinstance(node, BoolOp):
            left, right = self.compile(node.left), self.compile(node.right)
            return (left & right) if node.op == "AND" else (left | right)
        if isinstance(node, Not):
            return ~self.compile(node.operand)
        if isinstance(node, Comparison):
            left = self.operand(node.left, node.right)
            right = self.operand(node.right, node.left)
            return {
                "=": left.__eq__, "!=": left.__ne__, "<": left.__lt__,
                "<=": left.__le__, ">": left.__gt__, ">=": left.__ge__,
            }[node.op](right)  # fmt: skip
        if isinstance(node, IsNull):
            expression = self.operand(node.operand).is_null()
            return ~expression if node.negated else expression
        if isinstance(node, InList):
            target = self.field_type(node.operand)
            values = [_scalar(v.value, target) for v in node.values]
            value_set = pa.array([v.as_py() for v in values], type=target or values[0].type)
            operand = self.operand(node.operand)
            expression = operand.isin(value_set)
            # isin() is false (not null) for nulls, so NOT IN must exclude them itself.
            return (~expression & operand.is_valid()) if node.negated else expression
        if isinstance(node, Between):
            value = self.operand(node.operand)
            low = self.operand(node.low, node.operand)
            high = self.operand(node.high, node.operand)
            expression = (value >= low) & (value <= high)
            return ~expression if node.negated else expression
        if isinstance(node, Like):
            expression = pc.match_like(self.operand(node.operand), node.pattern)
            return ~expression if node.negated else expression
        raise SQLError(f"Unsupported condition {node!r}.")  # pragma: no cover


def compile_filter(condition: Condition, schema: pa.Schema) -> pc.Expression:
    return _Compiler(schema).compile(condition)
