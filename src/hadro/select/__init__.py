"""SelectObjectContent (S3 Select) over Parquet objects.

https://docs.aws.amazon.com/AmazonS3/latest/API/API_SelectObjectContent.html
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from xml.etree import ElementTree as ET

import pyarrow as pa
import pyarrow.parquet as pq

from ..errors import S3Error
from ..s3xml import find, find_text
from . import eventstream
from .formats import OutputFormat, serialise
from .sql import SQLError, compile_filter, parse, resolve_column

BATCH_ROWS = 10_000


def parse_request(body: bytes):
    """Return (expression, OutputFormat) from a SelectObjectContentRequest body."""
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

    input_serialization = find(root, "InputSerialization")
    if input_serialization is None:
        raise S3Error("MissingRequiredParameter", "InputSerialization is required.")
    if find(input_serialization, "Parquet") is None:
        raise S3Error(
            "UnsupportedFormat",
            "hadro only supports S3 Select over Parquet; use GetObject for other formats.",
        )
    compression = (find_text(input_serialization, "CompressionType") or "NONE").upper()
    if compression != "NONE":
        raise S3Error("InvalidCompressionFormat", "Parquet input must use CompressionType NONE.")

    output = OutputFormat()
    output_serialization = find(root, "OutputSerialization")
    csv_node = find(output_serialization, "CSV")
    parquet_node = find(output_serialization, "Parquet")
    json_node = find(output_serialization, "JSON")
    if csv_node is not None:
        output.format = "csv"
        output.field_delimiter = find_text(csv_node, "FieldDelimiter", strip=False) or ","
        output.record_delimiter = find_text(csv_node, "RecordDelimiter", strip=False) or "\n"
        output.quote_character = find_text(csv_node, "QuoteCharacter", strip=False) or '"'
        output.quote_fields = find_text(csv_node, "QuoteFields") or "ASNEEDED"
    elif parquet_node is not None:
        output.format = "parquet"
        output.compression = find_text(parquet_node, "CompressionAlgorithm") or "snappy"
        level = find_text(parquet_node, "CompressionLevel")
        output.compression_level = int(level) if level else None
        output.write_statistics = (find_text(parquet_node, "WriteStatistics") or "").lower() in (
            "true",
            "1",
        )
    elif json_node is not None:
        output.record_delimiter = find_text(json_node, "RecordDelimiter", strip=False) or "\n"
    return expression, output


def execute(content: bytes, expression: str, output: OutputFormat) -> Iterator[bytes]:
    """Run the query and return the event stream.

    Errors in the request raise ``S3Error`` before anything is streamed.
    """
    try:
        query = parse(expression)
    except SQLError as exc:
        raise S3Error("ParseUnexpectedToken", str(exc)) from None

    # Fast path: SELECT * with no filter asked for as Parquet returns the file untouched.
    if (
        output.format == "parquet"
        and query.projection is None
        and query.where is None
        and query.limit is None
    ):
        return iter(
            [eventstream.records(content), _stats(content, len(content)), eventstream.end()]
        )

    try:
        schema = pq.read_schema(io.BytesIO(content))
    except pa.ArrowException as exc:
        raise S3Error(
            "InvalidParquetFile", f"The object is not a valid Parquet file: {exc}"
        ) from None

    try:
        filters = compile_filter(query.where, schema) if query.where is not None else None
        projection = (
            None
            if query.projection is None
            else [(resolve_column(schema, column), name) for column, name in query.projection]
        )
    except SQLError as exc:
        raise S3Error("InvalidQuery", str(exc)) from None

    columns = None
    if projection is not None:
        columns = list(dict.fromkeys(source for source, _ in projection))

    try:
        table = pq.read_table(io.BytesIO(content), columns=columns, filters=filters)
    except (pa.ArrowException, ValueError) as exc:
        raise S3Error("InvalidQuery", f"Error executing query: {exc}") from None

    if query.limit is not None:
        table = table.slice(0, query.limit)
    if projection is not None:
        table = pa.table(
            [table.column(source) for source, _ in projection],
            names=[name for _, name in projection],
        )

    return _stream(table, content, output)


def _stats(content: bytes, returned: int) -> bytes:
    return eventstream.stats(len(content), len(content), returned)


def _stream(table: pa.Table, content: bytes, output: OutputFormat) -> Iterator[bytes]:
    returned = 0
    try:
        if not output.batchable:
            payload = serialise(table, output)
            returned += len(payload)
            yield eventstream.records(payload)
        else:
            for offset in range(0, table.num_rows, BATCH_ROWS):
                payload = serialise(table.slice(offset, BATCH_ROWS), output)
                returned += len(payload)
                yield eventstream.records(payload)
    except Exception as exc:  # noqa: BLE001 - reported to the client in-stream
        yield eventstream.error("InternalError", f"Error serialising results: {exc}")
        return
    yield _stats(content, returned)
    yield eventstream.end()
