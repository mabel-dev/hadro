"""Output serialisation for S3 Select results."""

from __future__ import annotations

import base64
import csv
import io
import json
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq


@dataclass
class OutputFormat:
    format: str = "json"  # json, csv or parquet
    field_delimiter: str = ","
    record_delimiter: str = "\n"
    quote_character: str = '"'
    quote_fields: str = "ASNEEDED"  # or ALWAYS
    # Parquet output is a hadro extension (it is not part of AWS S3 Select).
    compression: str | None = "snappy"
    compression_level: int | None = None
    write_statistics: bool = False

    @property
    def batchable(self) -> bool:
        # A Parquet file cannot be split across Records events and concatenated.
        return self.format != "parquet"


def _json_default(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return base64.b64encode(raw).decode("ascii")
    raise TypeError(f"Cannot serialise {type(value).__name__} to JSON")


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, default=_json_default)
    if isinstance(value, (bytes, bytearray, memoryview)) or not isinstance(
        value, (str, int, float, bool)
    ):
        return _json_default(value)
    return value


def to_json(table: pa.Table, output: OutputFormat) -> bytes:
    delimiter = output.record_delimiter
    rows = table.to_pylist()
    return "".join(
        json.dumps(row, default=_json_default, ensure_ascii=False) + delimiter for row in rows
    ).encode("utf-8")


def to_csv(table: pa.Table, output: OutputFormat) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(
        buffer,
        delimiter=output.field_delimiter,
        lineterminator=output.record_delimiter,
        quotechar=output.quote_character,
        quoting=csv.QUOTE_ALL if output.quote_fields.upper() == "ALWAYS" else csv.QUOTE_MINIMAL,
    )
    columns = [column.to_pylist() for column in table.columns]
    for row in zip(*columns):
        writer.writerow([_csv_value(value) for value in row])
    return buffer.getvalue().encode("utf-8")


def to_parquet(table: pa.Table, output: OutputFormat) -> bytes:
    buffer = io.BytesIO()
    pq.write_table(
        table,
        buffer,
        compression=output.compression,
        compression_level=output.compression_level,
        write_statistics=output.write_statistics,
    )
    return buffer.getvalue()


def serialise(table: pa.Table, output: OutputFormat) -> bytes:
    if output.format == "csv":
        return to_csv(table, output)
    if output.format == "parquet":
        return to_parquet(table, output)
    return to_json(table, output)
