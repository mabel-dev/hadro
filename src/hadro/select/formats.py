"""Input and output serialisation for S3 Select, using rugo."""

from __future__ import annotations

import base64
import csv
import io
import json
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from rugo import csv as rugo_csv
from rugo import jsonl as rugo_jsonl
from rugo import parquet as rugo_parquet


@dataclass
class InputFormat:
    format: str = "parquet"  # parquet, json (lines) or csv
    compression: str = "NONE"  # NONE, GZIP or BZIP2 (CSV and JSON only)
    file_header_info: str = "NONE"  # CSV: USE, IGNORE or NONE
    field_delimiter: str = ","


@dataclass
class OutputFormat:
    format: str = "json"  # json, csv or parquet
    field_delimiter: str = ","
    record_delimiter: str = "\n"
    quote_character: str = '"'
    quote_fields: str = "ASNEEDED"  # or ALWAYS
    # Parquet output is a hadro extension (it is not part of AWS S3 Select).
    compression: str = "zstd"  # rugo writes zstd or none

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
    if isinstance(value, (str, int, float, bool)):
        return value
    return _json_default(value)


def _rows(morsel):
    names = [name.decode() for name in morsel.column_names]
    columns = [morsel.column(name).to_pylist() for name in morsel.column_names]
    return names, zip(*columns)


def to_json(morsel, output: OutputFormat) -> bytes:
    if output.record_delimiter == "\n":
        return rugo_jsonl.write_jsonl(morsel)
    names, rows = _rows(morsel)
    return "".join(
        json.dumps(dict(zip(names, row)), default=_json_default, ensure_ascii=False)
        + output.record_delimiter
        for row in rows
    ).encode("utf-8")


def to_csv(morsel, output: OutputFormat) -> bytes:
    if (
        output.record_delimiter == "\n"
        and output.quote_character == '"'
        and output.quote_fields.upper() == "ASNEEDED"
        and len(output.field_delimiter) == 1
    ):
        return rugo_csv.write_csv(morsel, delimiter=output.field_delimiter, header=False)
    _, rows = _rows(morsel)
    buffer = io.StringIO()
    writer = csv.writer(
        buffer,
        delimiter=output.field_delimiter,
        lineterminator=output.record_delimiter,
        quotechar=output.quote_character,
        quoting=csv.QUOTE_ALL if output.quote_fields.upper() == "ALWAYS" else csv.QUOTE_MINIMAL,
    )
    for row in rows:
        writer.writerow([_csv_value(value) for value in row])
    return buffer.getvalue().encode("utf-8")


def to_parquet(morsel, output: OutputFormat) -> bytes:
    return rugo_parquet.write_parquet(morsel, compression=output.compression)


def serialise(morsel, output: OutputFormat) -> bytes:
    if output.format == "csv":
        return to_csv(morsel, output)
    if output.format == "parquet":
        return to_parquet(morsel, output)
    return to_json(morsel, output)
