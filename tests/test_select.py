import json
import struct

import pytest
from botocore.exceptions import ClientError
from rugo import jsonl as rugo_jsonl
from rugo import parquet as rugo_parquet

from hadro.select import BATCH_ROWS, execute
from hadro.select.evaluate import compile_mask, conjuncts, plan
from hadro.select.formats import InputFormat, OutputFormat
from hadro.select.sql import SQLError, parse

from . import reference

PARQUET = {"Parquet": {}}


def _select(s3, sql, output=None, bucket="astronauts", key="astronauts.parquet", source=None):
    response = s3.select_object_content(
        Bucket=bucket,
        Key=key,
        Expression=sql,
        ExpressionType="SQL",
        InputSerialization=source or PARQUET,
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


def _rows(s3, sql, **kwargs):
    payload, _ = _select(s3, sql, **kwargs)
    return [json.loads(line) for line in payload.decode().splitlines()]


def _morsel_rows(morsels) -> list[dict]:
    rows = []
    for morsel in morsels:
        names = [n.decode() for n in morsel.column_names]
        columns = [morsel.column(n).to_pylist() for n in morsel.column_names]
        rows.extend(dict(zip(names, values)) for values in zip(*columns))
    return rows


@pytest.fixture(scope="module")
def astronauts(data_dir):
    """Expected results come from plain Python over the rows rugo decodes."""
    with rugo_parquet.read_parquet(str(data_dir / "astronauts/astronauts.parquet")) as reader:
        return _morsel_rows(reader)


def test_select_star(s3, astronauts):
    rows = _rows(s3, "SELECT * FROM S3Object")
    assert len(rows) == len(astronauts)
    assert set(rows[0]) == set(astronauts[0])


def test_projection_and_filter(s3, astronauts):
    rows = _rows(s3, "SELECT name, space_flights FROM S3Object WHERE space_flights > 5")
    expected = [r for r in astronauts if r["space_flights"] > 5]
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
        for r in astronauts
        if (r["space_flights"] >= 6 or r["space_walks"] > 8) and r["gender"] != "Female"
    ]
    assert [r["name"] for r in _rows(s3, sql)] == expected


def test_in_between_like_null(s3, astronauts):
    rows = _rows(s3, "SELECT name FROM S3Object WHERE year IN (1996, 1998)")
    assert len(rows) == sum(r["year"] in (1996, 1998) for r in astronauts)
    rows = _rows(s3, "SELECT name FROM S3Object WHERE space_flights BETWEEN 2 AND 3")
    assert len(rows) == sum(2 <= r["space_flights"] <= 3 for r in astronauts)
    rows = _rows(s3, "SELECT name FROM S3Object WHERE name LIKE 'John%'")
    assert len(rows) == sum(r["name"].startswith("John") for r in astronauts)
    rows = _rows(s3, "SELECT name FROM S3Object WHERE death_date IS NOT NULL")
    assert len(rows) == sum(r["death_date"] is not None for r in astronauts)
    rows = _rows(s3, "SELECT name FROM S3Object WHERE year NOT IN (1996)")
    assert len(rows) == sum(r["year"] is not None and r["year"] != 1996 for r in astronauts)


def test_date_literal_is_coerced(s3, astronauts):
    rows = _rows(s3, "SELECT name, birth_date FROM S3Object WHERE birth_date < '1930-01-01'")
    expected = [r for r in astronauts if r["birth_date"] and str(r["birth_date"]) < "1930"]
    assert len(rows) == len(expected) > 0
    assert rows[0]["birth_date"] < "1930-01-01"


def test_alias_limit_and_case(s3):
    rows = _rows(s3, 'SELECT s.NAME AS who, s."space_flights" flights FROM S3Object AS s LIMIT 2;')
    assert len(rows) == 2 and set(rows[0]) == {"who", "flights"}


def test_limit_zero_and_no_matches(s3):
    assert _rows(s3, "SELECT name FROM S3Object LIMIT 0") == []
    assert _rows(s3, "SELECT name FROM S3Object WHERE space_flights > 1000") == []


def test_nested_and_binary_values_serialise(s3):
    rows = _rows(s3, "SELECT missions, birth_place FROM S3Object LIMIT 5")
    assert all(isinstance(r["missions"], (list, type(None))) for r in rows)
    assert all(isinstance(r["birth_place"], (str, type(None))) for r in rows)


def test_pushdown_plan():
    names = ["a", "b", "d", "t", "l"]
    kinds = {"a": "int", "b": "str", "d": "date", "t": "datetime", "l": "other"}
    where = parse(
        "SELECT * FROM S3Object WHERE a > 1 AND 'x' = b AND d < '2020-01-01' "
        "AND a IN (1, 2) AND NOT a IN (5) AND b IS NULL AND NOT d IS NULL "
        "AND a BETWEEN 1 AND 9 AND NOT a >= 7 AND t > '2020-01-01T00:00:00' "
        "AND (a = 1 OR b = 'y') AND b LIKE 'x%' AND a NOT BETWEEN 2 AND 3 "
        "AND l = 'x' AND a IN (1, NULL) AND a = b"
    ).where
    pushed, residual = plan(where, names, kinds, "parquet")
    assert [(c, op) for c, op, _ in pushed] == [
        ("a", ">"), ("b", "=="), ("d", "<"), ("a", "in"), ("a", "not in"),
        ("b", "is null"), ("d", "is not null"), ("a", ">="), ("a", "<="), ("a", "<"), ("t", ">"),
    ]  # fmt: skip
    assert str(pushed[2][2]) == "2020-01-01" and pushed[10][2].tzinfo is not None
    # OR, LIKE, NOT BETWEEN, list columns, NULL list members and column-to-column stay native.
    assert len(conjuncts(residual)) == 6

    json_kinds = {"a": "int", "b": "str", "d": "str", "t": "str", "l": "other"}
    json_pushed, _ = plan(where, names, json_kinds, "json")
    assert {op for _, op, _ in json_pushed} >= {"in", "not in", "is null", "is not null"}

    # CSV types are unknown up front: comparisons are pushed with the literal as written.
    csv_pushed, csv_residual = plan(where, names, {}, "csv")
    assert ("a", ">", 1) in csv_pushed and ("b", "==", "x") in csv_pushed
    assert ("d", "<", "2020-01-01") in csv_pushed
    assert {op for _, op, _ in csv_pushed} <= {"==", "!=", "<", "<=", ">", ">="}
    assert csv_residual is not None


def test_mask_three_valued_logic():
    with rugo_jsonl.read_jsonl(
        b'{"x": null, "y": "a"}\n{"x": 3, "y": "abc"}\n{"x": 1, "y": null}\n'
    ) as reader:
        morsel = next(iter(reader))

    def mask(where):
        return compile_mask(
            parse(f"SELECT * FROM S3Object WHERE {where}").where, morsel
        ).to_pylist()

    assert mask("x > 1") == [None, True, False]
    assert mask("NOT x > 1") == [None, False, True]
    assert mask("x > 1 OR y = 'a'") == [True, True, None]
    assert mask("x > 1 AND y = 'b'") == [False, False, False]  # NULL AND FALSE is FALSE
    assert mask("x IS NULL") == [True, False, False]
    assert mask("x NOT IN (1, 2)") == [None, True, False]
    assert mask("x NOT IN (1, NULL)") == [None, None, False]
    assert mask("x IN (3, NULL)") == [None, True, None]
    assert mask("y LIKE 'a%'") == [True, True, None]
    assert mask("y LIKE 'a_c'") == [False, True, None]
    assert mask("y NOT LIKE '%'") == [False, False, None]
    assert mask("x BETWEEN 1 AND 2") == [None, False, True]
    assert mask("1 = NULL") == [None, None, None]


def test_reference_three_valued_logic():
    """The Python oracle itself must follow SQL's rules."""
    names, kinds = ["x", "y"], {"x": "int", "y": "str"}

    def check(where, row):
        condition = reference.compile_condition(
            parse(f"SELECT * FROM S3Object WHERE {where}").where, names, kinds
        )
        return condition(row)

    null = {"x": None, "y": "a"}
    assert check("x > 1", null) is None
    assert check("NOT x > 1", null) is None
    assert check("x > 1 OR y = 'a'", null) is True
    assert check("x > 1 AND y = 'b'", null) is False
    assert check("x IS NULL", null) is True
    assert check("x NOT IN (1, 2)", null) is None
    assert check("x NOT IN (1, NULL)", {"x": 3, "y": "a"}) is None
    assert check("x IN (3, NULL)", {"x": 3, "y": "a"}) is True
    assert check("y LIKE 'a_c%'", {"x": 1, "y": "abcdef"}) is True
    assert check("y LIKE 'a.c'", {"x": 1, "y": "abc"}) is False


def test_csv_output(s3):
    payload, _ = _select(
        s3,
        "SELECT name, space_flights FROM S3Object WHERE space_flights > 6",
        {"CSV": {"FieldDelimiter": "\t", "QuoteFields": "ALWAYS"}},
    )
    lines = payload.decode().splitlines()
    assert lines and all(line.count("\t") == 1 for line in lines)
    assert lines[0].startswith('"')
    payload, _ = _select(s3, "SELECT name FROM S3Object LIMIT 3", {"CSV": {}})
    assert payload.decode().count("\n") == 3


def test_json_output_record_delimiter(s3):
    payload, _ = _select(
        s3, "SELECT name FROM S3Object LIMIT 3", {"JSON": {"RecordDelimiter": ";"}}
    )
    assert payload.decode().count(";") == 3 and "\n" not in payload.decode()


def test_parquet_output(client, data_dir):
    """Parquet output is a hadro extension; boto3 does not allow it, so use raw XML."""
    body = _request_xml(
        "SELECT name FROM S3Object WHERE space_flights > 5",
        "<Parquet><CompressionAlgorithm>zstd</CompressionAlgorithm></Parquet>",
    )
    result = _records(client.post(_url(), content=body).content)
    with rugo_parquet.read_parquet(result) as reader:
        rows = _morsel_rows(reader)
    assert rows and set(rows[0]) == {"name"}

    # SELECT * with Parquet output returns the original file untouched.
    body = _request_xml("SELECT * FROM S3Object", "<Parquet/>")
    raw = _records(client.post(_url(), content=body).content)
    assert raw == (data_dir / "astronauts/astronauts.parquet").read_bytes()

    body = _request_xml(
        "SELECT * FROM S3Object LIMIT 1",
        "<Parquet><CompressionAlgorithm>snappy</CompressionAlgorithm></Parquet>",
    )
    assert client.post(_url(), content=body).status_code == 400


def test_large_results_are_batched():
    lines = b"".join(b'{"n": %d}\n' % i for i in range(BATCH_ROWS * 2 + 5))
    with rugo_jsonl.read_jsonl(lines) as reader:
        content = rugo_parquet.write_parquet(next(iter(reader)))
    events = list(execute(content, "SELECT n FROM S3Object", InputFormat(), OutputFormat()))
    assert len(events) == 3 + 2  # three Records, Stats, End


def test_stats(s3, data_dir):
    payload, stats = _select(s3, "SELECT name FROM S3Object LIMIT 1")
    size = (data_dir / "astronauts/astronauts.parquet").stat().st_size
    assert stats == {"BytesScanned": size, "BytesProcessed": size, "BytesReturned": len(payload)}


# --- JSON Lines and CSV input -----------------------------------------------


JSON_LINES = {"JSON": {"Type": "LINES"}}


def test_json_lines_input(s3):
    rows = _rows(
        s3,
        "SELECT name, score FROM S3Object WHERE score >= 50 AND team <> 'blue' LIMIT 10",
        bucket="events",
        key="events.jsonl",
        source=JSON_LINES,
    )
    assert rows == [{"name": "carol", "score": 72}, {"name": "erin", "score": 91}]


def test_json_lines_gzip_input(s3):
    rows = _rows(
        s3,
        "SELECT name FROM S3Object WHERE tags IS NULL",
        bucket="events",
        key="events.jsonl.gz",
        source={**JSON_LINES, "CompressionType": "GZIP"},
    )
    assert rows == [{"name": "dave"}]


def test_json_document_input_is_rejected(s3):
    with pytest.raises(ClientError) as err:
        _select(
            s3,
            "SELECT * FROM S3Object",
            bucket="events",
            key="events.jsonl",
            source={"JSON": {"Type": "DOCUMENT"}},
        )
    assert err.value.response["Error"]["Code"] == "UnsupportedFormat"


def test_csv_input_with_header(s3):
    rows = _rows(
        s3,
        "SELECT name, age FROM S3Object WHERE age > 30 AND city LIKE 'L%'",
        bucket="events",
        key="people.csv",
        source={"CSV": {"FileHeaderInfo": "USE"}},
    )
    assert rows == [{"name": "Ada", "age": 36}, {"name": "Grace", "age": 45}]


@pytest.mark.parametrize("header", ["NONE", "IGNORE"])
def test_csv_input_positional_columns(s3, header):
    key = "people.csv" if header == "IGNORE" else "people_noheader.csv"
    rows = _rows(
        s3,
        "SELECT _1 AS name FROM S3Object WHERE _2 < 30",
        bucket="events",
        key=key,
        source={"CSV": {"FileHeaderInfo": header}},
    )
    assert rows == [{"name": "Linus"}]


def test_csv_input_other_delimiter(s3):
    rows = _rows(
        s3,
        "SELECT city FROM S3Object WHERE name = 'Linus'",
        bucket="events",
        key="people.tsv",
        source={"CSV": {"FileHeaderInfo": "USE", "FieldDelimiter": "\t"}},
    )
    assert rows == [{"city": "Helsinki, FI"}]


def test_csv_backslash_in_quoted_field(s3):
    """tweets.csv has quoted fields containing backslashes (RFC 4180 has no escapes)."""
    rows = _rows(
        s3,
        "SELECT username FROM S3Object WHERE username = 'wugeej' LIMIT 1",
        bucket="tweets",
        key="tweets.csv",
        source={"CSV": {"FileHeaderInfo": "USE"}},
    )
    assert rows == [{"username": "wugeej"}]


# --- errors -----------------------------------------------------------------


@pytest.mark.parametrize(
    "sql, code",
    [
        ("SELECT nope FROM S3Object", "InvalidQuery"),
        ("SELECT name FROM S3Object WHERE nope = 1", "InvalidQuery"),
        ("SELECT name FROM S3Object WHERE year = 'abc'", "InvalidQuery"),
        ("SELECT name FROM S3Object WHERE missions = 'x'", "InvalidQuery"),
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
    no_format = _request_xml("SELECT * FROM S3Object", "<JSON/>").replace(b"<Parquet/>", b"")
    response = client.post(_url(), content=no_format)
    assert response.status_code == 400 and b"UnsupportedFormat" in response.content
    response = client.post(
        "/astronauts/missing.parquet?select&select-type=2",
        content=_request_xml("SELECT * FROM S3Object", "<JSON/>"),
    )
    assert response.status_code == 404 and b"NoSuchKey" in response.content


def test_wrong_input_format(client):
    response = client.post(
        "/events/events.jsonl?select&select-type=2",
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
    payload, offset = b"", 0
    while offset < len(stream):
        total, header_length = struct.unpack(">II", stream[offset : offset + 8])
        headers = stream[offset + 12 : offset + 12 + header_length]
        body = stream[offset + 12 + header_length : offset + total - 4]
        if b"Records" in headers:
            payload += body
        offset += total
    return payload
