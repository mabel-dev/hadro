import io
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from botocore.exceptions import ClientError

from hadro.select.sql import SQLError, compile_filter, parse


def _select(s3, sql, output=None, bucket="astronauts", key="astronauts.parquet"):
    response = s3.select_object_content(
        Bucket=bucket,
        Key=key,
        Expression=sql,
        ExpressionType="SQL",
        InputSerialization={"Parquet": {}},
        OutputSerialization=output or {"JSON": {}},
    )
    payload, stats, ended = b"", None, False
    for event in response["Payload"]:
        if "Records" in event:
            payload += event["Records"]["Payload"]
        elif "Stats" in event:
            stats = event["Stats"]["Details"]
        elif "End" in event:
            ended = True
    assert ended
    return payload, stats


def _rows(s3, sql):
    payload, _ = _select(s3, sql)
    return [json.loads(line) for line in payload.decode().splitlines()]


@pytest.fixture(scope="module")
def astronauts(data_dir):
    return pq.read_table(data_dir / "astronauts/astronauts.parquet")


def test_select_star(s3, astronauts):
    rows = _rows(s3, "SELECT * FROM S3Object")
    assert len(rows) == astronauts.num_rows
    assert set(rows[0]) == set(astronauts.column_names)


def test_projection_and_filter(s3, astronauts):
    rows = _rows(s3, "SELECT name, space_flights FROM S3Object WHERE space_flights > 5")
    expected = [r for r in astronauts.to_pylist() if r["space_flights"] > 5]
    assert [r["name"] for r in rows] == [r["name"] for r in expected]
    assert all(set(r) == {"name", "space_flights"} for r in rows)


def test_filter_on_column_not_projected(s3):
    rows = _rows(s3, "SELECT name FROM S3Object WHERE status = 'Deceased'")
    assert rows and all(set(r) == {"name"} for r in rows)


def test_boolean_logic(s3, astronauts):
    sql = (
        "SELECT name FROM S3Object WHERE (space_flights >= 6 OR space_walks > 8) "
        "AND NOT gender = 'Female'"
    )
    expected = [
        r["name"]
        for r in astronauts.to_pylist()
        if (r["space_flights"] >= 6 or r["space_walks"] > 8) and r["gender"] != "Female"
    ]
    assert [r["name"] for r in _rows(s3, sql)] == expected


def test_in_between_like_null(s3, astronauts):
    data = astronauts.to_pylist()
    rows = _rows(s3, "SELECT name FROM S3Object WHERE year IN (1996, 1998)")
    assert len(rows) == sum(r["year"] in (1996, 1998) for r in data)
    rows = _rows(s3, "SELECT name FROM S3Object WHERE space_flights BETWEEN 2 AND 3")
    assert len(rows) == sum(2 <= r["space_flights"] <= 3 for r in data)
    rows = _rows(s3, "SELECT name FROM S3Object WHERE name LIKE 'John%'")
    assert len(rows) == sum(r["name"].startswith("John") for r in data)
    rows = _rows(s3, "SELECT name FROM S3Object WHERE death_date IS NOT NULL")
    assert len(rows) == sum(r["death_date"] is not None for r in data)
    rows = _rows(s3, "SELECT name FROM S3Object WHERE year NOT IN (1996)")
    assert len(rows) == sum(r["year"] is not None and r["year"] != 1996 for r in data)


def test_date_literal_is_coerced(s3, astronauts):
    rows = _rows(s3, "SELECT name, birth_date FROM S3Object WHERE birth_date < '1930-01-01'")
    expected = [
        r for r in astronauts.to_pylist() if r["birth_date"] and str(r["birth_date"]) < "1930"
    ]
    assert len(rows) == len(expected) > 0
    assert rows[0]["birth_date"] < "1930-01-01"


def test_alias_limit_and_case(s3):
    rows = _rows(s3, 'SELECT s.NAME AS who, s."space_flights" flights FROM S3Object AS s LIMIT 2;')
    assert len(rows) == 2 and set(rows[0]) == {"who", "flights"}


def test_nested_and_binary_values_serialise(s3):
    rows = _rows(s3, "SELECT missions, birth_place FROM S3Object LIMIT 5")
    assert all(isinstance(r["missions"], (list, type(None))) for r in rows)


def test_csv_output(s3):
    payload, _ = _select(
        s3,
        "SELECT name, space_flights FROM S3Object WHERE space_flights > 6",
        {"CSV": {"FieldDelimiter": "\t", "QuoteFields": "ALWAYS"}},
    )
    lines = payload.decode().splitlines()
    assert lines and all(line.count("\t") == 1 for line in lines)
    assert lines[0].startswith('"')


def test_parquet_output(client, data_dir):
    """Parquet output is a hadro extension; boto3 does not allow it, so use raw XML."""
    body = _request_xml(
        "SELECT name FROM S3Object WHERE space_flights > 5",
        "<Parquet><CompressionAlgorithm>zstd</CompressionAlgorithm></Parquet>",
    )
    table = pq.read_table(io.BytesIO(_records(client.post(_url(), content=body).content)))
    assert table.column_names == ["name"] and table.num_rows > 0

    # SELECT * with Parquet output returns the original file untouched.
    body = _request_xml("SELECT * FROM S3Object", "<Parquet/>")
    raw = _records(client.post(_url(), content=body).content)
    assert raw == (data_dir / "astronauts/astronauts.parquet").read_bytes()


def test_large_results_are_batched(tmp_path):
    from hadro.select import BATCH_ROWS, execute
    from hadro.select.formats import OutputFormat

    buffer = io.BytesIO()
    pq.write_table(pa.table({"n": list(range(BATCH_ROWS * 2 + 5))}), buffer)
    events = list(execute(buffer.getvalue(), "SELECT n FROM S3Object", OutputFormat()))
    assert len(events) == 3 + 2  # three Records, Stats, End


def test_stats(s3, data_dir):
    payload, stats = _select(s3, "SELECT name FROM S3Object LIMIT 1")
    size = (data_dir / "astronauts/astronauts.parquet").stat().st_size
    assert stats == {"BytesScanned": size, "BytesProcessed": size, "BytesReturned": len(payload)}


@pytest.mark.parametrize(
    "sql, code",
    [
        ("SELECT nope FROM S3Object", "InvalidQuery"),
        ("SELECT name FROM S3Object WHERE nope = 1", "InvalidQuery"),
        ("SELECT name FROM S3Object WHERE year = 'abc'", "InvalidQuery"),
        ("SELECT COUNT(*) FROM S3Object", "ParseUnexpectedToken"),
        ("SELECT name FROM other", "ParseUnexpectedToken"),
        ("SELECT name FROM S3Object GROUP BY name", "ParseUnexpectedToken"),
        ("SELECT x.name FROM S3Object s", "ParseUnexpectedToken"),
    ],
)
def test_query_errors(s3, sql, code):
    with pytest.raises(ClientError) as err:
        _select(s3, sql)
    assert err.value.response["Error"]["Code"] == code


def test_request_errors(client):
    assert client.post(_url(), content=b"<not xml").status_code == 400
    csv_input = _request_xml("SELECT * FROM S3Object", "<JSON/>").replace(b"<Parquet/>", b"<CSV/>")
    response = client.post(_url(), content=csv_input)
    assert response.status_code == 400 and b"UnsupportedFormat" in response.content
    response = client.post(
        "/astronauts/missing.parquet?select&select-type=2",
        content=_request_xml("SELECT * FROM S3Object", "<JSON/>"),
    )
    assert response.status_code == 404 and b"NoSuchKey" in response.content


def test_non_parquet_object(client):
    response = client.post(
        "/tweets/tweets.csv?select&select-type=2",
        content=_request_xml("SELECT * FROM S3Object WHERE a = 1", "<JSON/>"),
    )
    assert response.status_code == 400 and b"InvalidParquetFile" in response.content


# --- parser unit tests -------------------------------------------------------


def test_parse_shapes():
    query = parse("select a, b as c from s3object where a > 1 and not (b = 'x''y' or b is null)")
    assert [name for _, name in query.projection] == ["a", "c"]
    assert query.where is not None and query.limit is None


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "SELECT",
        "SELECT * FROM",
        "SELECT * FROM S3Object WHERE",
        "SELECT * FROM S3Object WHERE a >",
        "SELECT * FROM S3Object LIMIT -1",
        "SELECT * FROM S3Object LIMIT 1.5",
        "SELECT * FROM S3Object WHERE a NOT = 1",
        "SELECT * FROM S3Object[*]",
        "SELECT * FROM S3Object; DROP TABLE x",
        "SELECT a FROM S3Object WHERE a = @",
    ],
)
def test_parse_errors(sql):
    with pytest.raises(SQLError):
        parse(sql)


def test_compiled_filter_matches_python():
    table = pa.table({"x": [1, 2, 3, None], "y": ["a", "b", "c", "d"]})
    query = parse("SELECT * FROM S3Object WHERE x >= 2 OR y = 'a'")
    filtered = table.filter(compile_filter(query.where, table.schema))
    assert filtered.column("y").to_pylist() == ["a", "b", "c"]
    query = parse("SELECT * FROM S3Object WHERE 2 < x")
    assert table.filter(compile_filter(query.where, table.schema)).num_rows == 1
    query = parse("SELECT * FROM S3Object WHERE x > 1.5")
    assert table.filter(compile_filter(query.where, table.schema)).num_rows == 2


# --- helpers -----------------------------------------------------------------


def _url(key="astronauts.parquet"):
    return f"/astronauts/{key}?select&select-type=2"


def _request_xml(sql: str, output: str) -> bytes:
    return (
        '<SelectObjectContentRequest xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"<Expression>{sql}</Expression><ExpressionType>SQL</ExpressionType>"
        "<InputSerialization><Parquet/></InputSerialization>"
        f"<OutputSerialization>{output}</OutputSerialization>"
        "</SelectObjectContentRequest>"
    ).encode()


def _records(stream: bytes) -> bytes:
    """Concatenate Records payloads from a raw event stream."""
    import struct

    payload, offset = b"", 0
    while offset < len(stream):
        total, header_length = struct.unpack(">II", stream[offset : offset + 8])
        headers = stream[offset + 12 : offset + 12 + header_length]
        body = stream[offset + 12 + header_length : offset + total - 4]
        if b"Records" in headers:
            payload += body
        offset += total
    return payload
