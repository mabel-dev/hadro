import os
import sys
import pytest

sys.path.insert(1, os.path.join(sys.path[0], ".."))
sys.path.insert(1, os.path.join(sys.path[0], "../.."))


from hadro.memtable import MemTable
from hadro.exceptions import MaximumRecordsExceeded
import opteryx
import ormsgpack


def test_append_and_get_latest():
    data = opteryx.query("SELECT * FROM $missions;")
    schema = data.schema
    schema.primary_key = "Mission"

    table = MemTable(schema)

    for planet in data:
        table.append(planet.as_dict)

    print(table)


from tests.cities import schema


def test_append_record():
    memory_table = MemTable(schema)
    record = {
        "name": "Test City",
        "population": 500000,
        "country": "Testland",
        "founded": "2021",
        "area": 150.5,
        "language": "Testish",
    }
    memory_table.append(record)
    stored_record = memory_table._get("Test City")
    assert stored_record is not None
    assert set(ormsgpack.unpackb(stored_record[1])) == set(record.values())


def test_auto_flush():
    memory_table = MemTable(schema)
    # This assumes max_records is set to a smaller number for the test
    with pytest.raises(MaximumRecordsExceeded):
        for i in range(memory_table.max_records + 1):
            record = {
                "name": f"City {i}",
                "population": 100000 + i,
                "country": "Testland",
                "founded": str(2000 + i),
                "area": 100.0 + i,
                "language": "Testish",
            }
            memory_table.append(record)


def test_overwrite_record():
    memory_table = MemTable(schema)
    record1 = {
        "name": "Overwrite City",
        "population": 300000,
        "country": "Overland",
        "founded": "1900",
        "area": 300.0,
        "language": "Overish",
    }
    record2 = {
        "name": "Overwrite City",
        "population": 600000,  # New population
        "country": "Overland",
        "founded": "1900",
        "area": 300.0,
        "language": "Overish",
    }
    memory_table.append(record1)
    memory_table.append(record2)
    stored_record = memory_table._get("Overwrite City")
    assert stored_record is not None
    assert ormsgpack.unpackb(stored_record[1])[1] == record2["country"]


if __name__ == "__main__":
    test_append_and_get_latest()
    pytest.main()
