"""SelectObjectContent (S3 Select) over Parquet, JSON Lines and CSV objects.

https://docs.aws.amazon.com/AmazonS3/latest/API/API_SelectObjectContent.html

Objects are read with rugo; see ``evaluate`` for how filters are applied.
"""

from __future__ import annotations

import bz2
import csv
import gzip
import zlib
from collections.abc import Iterator
from xml.etree import ElementTree as ET

from draken import Morsel
from rugo import csv as rugo_csv
from rugo import jsonl as rugo_jsonl
from rugo import parquet as rugo_parquet

from ..errors import S3Error
from ..s3xml import find, find_text
from . import eventstream
from .evaluate import columns_used, compile_mask, kind, plan, resolve_column
from .formats import InputFormat, OutputFormat, serialise
from .sql import Query, SQLError, parse

BATCH_ROWS = 10_000


def parse_request(body: bytes) -> tuple[str, InputFormat, OutputFormat]:
    """Return (expression, input format, output format) from a request body."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise S3Error("MalformedXML", f"The XML you provided was not well-formed: {exc}") from None

    expression = find_text(root, "Expression")
    if not expression:
        raise S3Error("MissingRequiredParameter", "The Expression parameter is required.")
    expression_type = (find_text(root, "ExpressionType") or "SQL").upper()
    if expression_type != "SQL":
        raise S3Error("InvalidExpressionType", "ExpressionType must be SQL.")

    return expression, _parse_input(find(root, "InputSerialization")), _parse_output(root)


def _parse_input(node) -> InputFormat:
    if node is None:
        raise S3Error("MissingRequiredParameter", "InputSerialization is required.")
    source = InputFormat(compression=(find_text(node, "CompressionType") or "NONE").upper())
    if source.compression not in ("NONE", "GZIP", "BZIP2"):
        raise S3Error("InvalidCompressionFormat", f"Unknown CompressionType {source.compression}.")

    parquet_node, json_node, csv_node = find(node, "Parquet"), find(node, "JSON"), find(node, "CSV")
    if parquet_node is not None:
        source.format = "parquet"
        if source.compression != "NONE":
            raise S3Error(
                "InvalidCompressionFormat", "Parquet input must use CompressionType NONE."
            )
    elif json_node is not None:
        source.format = "json"
        if (find_text(json_node, "Type") or "DOCUMENT").upper() != "LINES":
            raise S3Error("UnsupportedFormat", "hadro only supports JSON input with Type LINES.")
    elif csv_node is not None:
        source.format = "csv"
        source.file_header_info = (find_text(csv_node, "FileHeaderInfo") or "NONE").upper()
        if source.file_header_info not in ("USE", "IGNORE", "NONE"):
            raise S3Error("InvalidFileHeaderInfo", "FileHeaderInfo must be USE, IGNORE or NONE.")
        source.field_delimiter = find_text(csv_node, "FieldDelimiter", strip=False) or ","
        record_delimiter = find_text(csv_node, "RecordDelimiter", strip=False) or "\n"
        quote = find_text(csv_node, "QuoteCharacter", strip=False) or '"'
        if len(source.field_delimiter) != 1 or record_delimiter != "\n" or quote != '"':
            raise S3Error(
                "UnsupportedFormat",
                "hadro reads CSV with a single-character FieldDelimiter, newline records "
                'and " quotes.',
            )
    else:
        raise S3Error("UnsupportedFormat", "InputSerialization must be Parquet, JSON or CSV.")
    return source


def _parse_output(root) -> OutputFormat:
    output = OutputFormat()
    node = find(root, "OutputSerialization")
    csv_node, parquet_node, json_node = find(node, "CSV"), find(node, "Parquet"), find(node, "JSON")
    if csv_node is not None:
        output.format = "csv"
        output.field_delimiter = find_text(csv_node, "FieldDelimiter", strip=False) or ","
        output.record_delimiter = find_text(csv_node, "RecordDelimiter", strip=False) or "\n"
        output.quote_character = find_text(csv_node, "QuoteCharacter", strip=False) or '"'
        output.quote_fields = find_text(csv_node, "QuoteFields") or "ASNEEDED"
    elif parquet_node is not None:
        output.format = "parquet"
        compression = (find_text(parquet_node, "CompressionAlgorithm") or "zstd").lower()
        if compression in ("none", "uncompressed"):
            compression = "none"
        if compression not in ("zstd", "none"):
            raise S3Error("InvalidRequest", "Parquet output supports ZSTD or no compression.")
        output.compression = compression
    elif json_node is not None:
        output.record_delimiter = find_text(json_node, "RecordDelimiter", strip=False) or "\n"
    return output


def execute(
    content: bytes,
    expression: str,
    source: InputFormat,
    output: OutputFormat,
    pushdown: bool = True,
) -> Iterator[bytes]:
    """Run the query and return the event stream.

    Errors in the request raise ``S3Error`` before anything is streamed.
    ``pushdown=False`` evaluates every condition in Python (used by the tests).
    """
    try:
        query = parse(expression)
    except SQLError as exc:
        raise S3Error("ParseUnexpectedToken", str(exc)) from None

    # Fast path: SELECT * with no filter, Parquet in and out, returns the file untouched.
    if (
        source.format == output.format == "parquet"
        and query.projection is None
        and query.where is None
        and query.limit is None
    ):
        return iter(
            [eventstream.records(content), _stats(content, len(content)), eventstream.end()]
        )

    scanned = len(content)
    content = _decompress(content, source.compression)
    try:
        morsel = _run(content, query, source, pushdown)
    except SQLError as exc:
        raise S3Error("InvalidQuery", str(exc)) from None
    return _stream(morsel, scanned, len(content), output)


def _decompress(content: bytes, compression: str) -> bytes:
    try:
        if compression == "GZIP":
            return gzip.decompress(content)
        if compression == "BZIP2":
            return bz2.decompress(content)
    except (OSError, EOFError, zlib.error) as exc:
        raise S3Error(
            "InvalidCompressionFormat", f"Could not decompress the object: {exc}"
        ) from None
    return content


def _run(content: bytes, query: Query, source: InputFormat, pushdown: bool) -> Morsel | None:
    names, kinds = _describe(content, source)

    projection = None
    if query.projection is not None:
        projection = [(resolve_column(names, column), alias) for column, alias in query.projection]
    needed = None
    if projection is not None:
        needed = list(dict.fromkeys([s for s, _ in projection] + columns_used(query.where, names)))

    predicates, residual = [], query.where
    if pushdown:
        predicates, residual = plan(query.where, names, kinds, source.format)

    try:
        morsel = _read(content, source, needed, predicates, names)
    except _PredicateRejected:
        # rugo refused a literal of the wrong type for the column (types are
        # inferred for CSV and JSONL); filter everything with Draken instead.
        morsel = _read(content, source, needed, [], names)
        residual = query.where
    if morsel is None:
        return None

    if residual is not None:
        morsel = morsel.filter_mask(compile_mask(residual, morsel))

    if query.limit is not None and query.limit < morsel.num_rows:
        morsel = morsel.take(list(range(query.limit)))
    if projection is not None:
        morsel = morsel.select([s for s, _ in projection]).rename([a for _, a in projection])
    return morsel


def _describe(content: bytes, source: InputFormat) -> tuple[list[str], dict[str, str]]:
    """Return the object's column names and, for Parquet, their kinds."""
    if source.format == "csv":
        header = _csv_header(content, source)
        if source.file_header_info == "USE":
            return header, {}
        return [f"_{i + 1}" for i in range(len(header))], {}
    try:
        if source.format == "parquet":
            columns = rugo_parquet.read_metadata(content).schema_columns
            return [c.name for c in columns], {c.name: kind(c.logical_type) for c in columns}
        columns = rugo_jsonl.read_metadata(content).schema_columns
        return [c["name"] for c in columns], {c["name"]: kind(c["type"]) for c in columns}
    except (RuntimeError, ValueError) as exc:
        raise _unreadable(source, exc) from None


class _PredicateRejected(Exception):
    pass


def _csv_header(content: bytes, source: InputFormat) -> list[str]:
    # rugo's CSV metadata has no delimiter or header options, so read the first line here.
    first_line = content.split(b"\n", 1)[0].decode("utf-8-sig", "replace").rstrip("\r")
    return next(csv.reader([first_line], delimiter=source.field_delimiter), [])


def _read(content, source: InputFormat, needed, predicates, names) -> Morsel | None:
    try:
        if source.format == "parquet":
            with rugo_parquet.read_parquet(
                content, columns=needed, predicates=predicates or None
            ) as reader:
                morsels = list(reader)
        elif source.format == "json":
            with rugo_jsonl.read_jsonl(
                content, columns=needed, predicates=predicates or None
            ) as reader:
                morsels = list(reader)
        else:
            has_header = source.file_header_info != "NONE"
            if predicates:
                # rugo names headerless columns col_0, col_1...; S3 calls them _1, _2...
                header = _csv_header(content, source)
                rugo_names = header if has_header else [f"col_{i}" for i in range(len(header))]
                by_s3_name = dict(zip(names, rugo_names))
                predicates = [(by_s3_name[c], op, v) for c, op, v in predicates]
            with rugo_csv.read_csv(
                content,
                predicates=predicates or None,
                delimiter=source.field_delimiter,
                has_header=has_header,
            ) as reader:
                morsels = list(reader)
    except (RuntimeError, ValueError, OSError) as exc:
        if predicates and isinstance(exc, ValueError):
            raise _PredicateRejected from exc
        raise _unreadable(source, exc) from None

    if not morsels:
        return None
    morsel = morsels[0] if len(morsels) == 1 else Morsel.combine(morsels)
    for name in morsel.column_names:
        if morsel.column(name) is None:
            raise S3Error("InvalidParquetFile", f"rugo could not decode column {name.decode()!r}.")
    if source.format == "csv":
        if morsel.num_columns != len(names):
            raise S3Error("InvalidTextEncoding", "CSV rows do not match the header's columns.")
        morsel = morsel.rename(names)  # S3 names: the header, or _1, _2...
    return morsel


def _unreadable(source: InputFormat, exc: Exception) -> S3Error:
    if source.format == "parquet":
        return S3Error("InvalidParquetFile", f"The object is not a readable Parquet file: {exc}")
    return S3Error("InvalidTextEncoding", f"The object could not be read as {source.format}: {exc}")


def _stats(scanned: int | bytes, returned: int, processed: int | None = None) -> bytes:
    scanned = scanned if isinstance(scanned, int) else len(scanned)
    return eventstream.stats(scanned, processed if processed is not None else scanned, returned)


def _stream(
    morsel: Morsel | None, scanned: int, processed: int, output: OutputFormat
) -> Iterator[bytes]:
    returned = 0
    try:
        if morsel is not None and morsel.num_rows:
            if not output.batchable:
                payload = serialise(morsel, output)
                returned += len(payload)
                yield eventstream.records(payload)
            else:
                for offset in range(0, morsel.num_rows, BATCH_ROWS):
                    count = min(BATCH_ROWS, morsel.num_rows - offset)
                    payload = serialise(morsel.take(list(range(offset, offset + count))), output)
                    returned += len(payload)
                    yield eventstream.records(payload)
    except Exception as exc:  # noqa: BLE001 - reported to the client in-stream
        yield eventstream.error("InternalError", f"Error serialising results: {exc}")
        return
    yield _stats(scanned, returned, processed)
    yield eventstream.end()
