"""hadro's native filtering must select exactly what plain Python selects.

Every query runs through hadro twice, with rugo predicate pushdown and with
Draken masks only, and both results must equal ``reference.select`` over the
same rows read without any filtering.
"""

import json

import pytest
from rugo import csv as rugo_csv
from rugo import jsonl as rugo_jsonl
from rugo import parquet as rugo_parquet

from hadro.select import execute
from hadro.select.evaluate import kind
from hadro.select.formats import InputFormat, OutputFormat, _json_default

from . import reference

ASTRONAUTS = [
    "SELECT name FROM S3Object WHERE space_flights > 5",
    "SELECT name FROM S3Object WHERE 3 < space_flights",
    "SELECT name FROM S3Object WHERE space_flights > 2.5",
    "SELECT name FROM S3Object WHERE space_flights = 4 AND year <= 1990",
    "SELECT name FROM S3Object WHERE NOT space_flights > 1",
    "SELECT name FROM S3Object WHERE NOT (space_flights > 1 AND year > 1990)",
    "SELECT name FROM S3Object WHERE status != 'Active'",
    "SELECT name FROM S3Object WHERE status <> 'Active' OR year IS NULL",
    "SELECT name FROM S3Object WHERE (space_flights >= 6 OR space_walks > 8) AND gender = 'Male'",
    "SELECT name FROM S3Object WHERE year IN (1996, 1998)",
    "SELECT name FROM S3Object WHERE year NOT IN (1996, 2004)",
    "SELECT name FROM S3Object WHERE NOT year IN (1996, 2004)",
    "SELECT name FROM S3Object WHERE year IN (1996, NULL)",
    "SELECT name FROM S3Object WHERE year NOT IN (1996, NULL)",
    "SELECT name FROM S3Object WHERE military_branch IN ('US Navy', 'US Air Force')",
    "SELECT name FROM S3Object WHERE space_flights BETWEEN 2 AND 3",
    "SELECT name FROM S3Object WHERE space_flights NOT BETWEEN 2 AND 3",
    "SELECT name FROM S3Object WHERE birth_date BETWEEN '1950-01-01' AND '1955-12-31'",
    "SELECT name FROM S3Object WHERE death_date IS NULL",
    "SELECT name FROM S3Object WHERE death_date IS NOT NULL",
    "SELECT name FROM S3Object WHERE NOT death_date IS NULL",
    "SELECT name FROM S3Object WHERE death_mission IS NULL AND death_date IS NOT NULL",
    "SELECT name FROM S3Object WHERE birth_date < '1930-01-01'",
    "SELECT name FROM S3Object WHERE birth_date >= '1960-01-01' AND name LIKE '%a%'",
    "SELECT name FROM S3Object WHERE name LIKE 'John%'",
    "SELECT name FROM S3Object WHERE name NOT LIKE 'John%'",
    "SELECT name FROM S3Object WHERE name LIKE 'Buzz Aldrin'",
    "SELECT name FROM S3Object WHERE name LIKE '%son'",
    "SELECT name FROM S3Object WHERE name LIKE 'J_hn%'",
    "SELECT name FROM S3Object WHERE name LIKE '%'",
    "SELECT name FROM S3Object WHERE military_rank LIKE 'Col%' OR military_rank IS NULL",
    "SELECT name FROM S3Object WHERE space_walks > space_flights",
    "SELECT name FROM S3Object WHERE space_walks_hours >= space_flight_hours",
    "SELECT name FROM S3Object WHERE 1 = 1 AND space_flights > 6",
    "SELECT name FROM S3Object WHERE 1 = NULL OR space_flights > 6",
    "SELECT name FROM S3Object WHERE NOT (1 = NULL) OR space_flights > 6",
    "SELECT name, year FROM S3Object WHERE year > 1990 LIMIT 7",
    "SELECT name FROM S3Object WHERE space_flights > 1000",
]

EVENTS = [
    "SELECT name FROM S3Object WHERE score >= 50",
    "SELECT name FROM S3Object WHERE score BETWEEN 40 AND 88",
    "SELECT name FROM S3Object WHERE team IN ('red', 'green')",
    "SELECT name FROM S3Object WHERE team NOT IN ('red')",
    "SELECT name FROM S3Object WHERE team != 'blue' AND score > 20",
    "SELECT name FROM S3Object WHERE tags IS NULL",
    "SELECT name FROM S3Object WHERE tags IS NOT NULL AND name LIKE '%a%'",
    "SELECT name FROM S3Object WHERE NOT score < 50 OR team = 'blue'",
]

PEOPLE = [
    "SELECT name FROM S3Object WHERE age > 30",
    "SELECT name FROM S3Object WHERE age != 36",
    "SELECT name FROM S3Object WHERE city LIKE 'L%'",
    "SELECT name FROM S3Object WHERE city LIKE '%, %' OR age BETWEEN 40 AND 50",
    "SELECT name FROM S3Object WHERE name IN ('Ada', 'Linus')",
]


def _read_all(content: bytes, source: InputFormat):
    """Every row with no filtering, plus column kinds, straight from rugo."""
    if source.format == "parquet":
        with rugo_parquet.read_parquet(content) as reader:
            morsels = list(reader)
    elif source.format == "json":
        with rugo_jsonl.read_jsonl(content) as reader:
            morsels = list(reader)
    else:
        with rugo_csv.read_csv(content) as reader:
            morsels = list(reader)
    rows, kinds = [], {}
    for morsel in morsels:
        names = [n.decode() for n in morsel.column_names]
        kinds.update(
            {(k.decode() if isinstance(k, bytes) else k): kind(t) for k, t in morsel.schema.items()}
        )
        columns = [morsel.column(n).to_pylist() for n in morsel.column_names]
        rows.extend(dict(zip(names, values)) for values in zip(*columns))
    return rows, kinds


def _hadro(content, sql, source, pushdown):
    stream = b"".join(execute(content, sql, source, OutputFormat(), pushdown=pushdown))
    payload, offset = b"", 0
    while offset < len(stream):
        total = int.from_bytes(stream[offset : offset + 4], "big")
        header_length = int.from_bytes(stream[offset + 4 : offset + 8], "big")
        headers = stream[offset + 12 : offset + 12 + header_length]
        if b"Records" in headers:
            payload += stream[offset + 12 + header_length : offset + total - 4]
        offset += total
    return [json.loads(line) for line in payload.decode().splitlines()]


def _check(content, sql, source):
    rows, kinds = _read_all(content, source)
    expected = json.loads(json.dumps(reference.select(rows, sql, kinds), default=_json_default))
    assert _hadro(content, sql, source, pushdown=False) == expected, "Draken masks"
    assert _hadro(content, sql, source, pushdown=True) == expected, "rugo pushdown"


@pytest.mark.parametrize("sql", ASTRONAUTS)
def test_parquet(data_dir, sql):
    _check((data_dir / "astronauts/astronauts.parquet").read_bytes(), sql, InputFormat())


@pytest.mark.parametrize("sql", EVENTS)
def test_json_lines(data_dir, sql):
    _check((data_dir / "events/events.jsonl").read_bytes(), sql, InputFormat(format="json"))


@pytest.mark.parametrize("sql", PEOPLE)
def test_csv(data_dir, sql):
    source = InputFormat(format="csv", file_header_info="USE")
    _check((data_dir / "events/people.csv").read_bytes(), sql, source)


def test_queries_select_something(data_dir):
    """Guard against a suite of vacuous comparisons of empty results."""
    content = (data_dir / "astronauts/astronauts.parquet").read_bytes()
    rows, kinds = _read_all(content, InputFormat())
    non_empty = sum(bool(reference.select(rows, sql, kinds)) for sql in ASTRONAUTS)
    assert non_empty >= len(ASTRONAUTS) - 2
