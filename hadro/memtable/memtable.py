"""


Each record is stored in a hashtable, where each record is a tuple of (Primary Key, Version, Bytes)


"""

import datetime
import time
from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple

import numpy
import ormsgpack
from orso.schema import RelationSchema

from hadro.exceptions import MaximumRecordsExceeded


class MemTable:
    """
    In-memory table structure designed to store records with versioning, acting as a Write-Ahead Log (WAL).
    This class manages an in-memory buffer of records, indexed by a primary key and supports automatic flushing
    of the buffer based on a maximum record count.

    Attributes:
        buffer (Dict[Any, Tuple[int, bytes]]): A dictionary that maps primary keys to tuples containing a timestamp
            (in nanoseconds) and the serialized bytes of the record.
        buffer_size (int): Total size of all records currently stored in the buffer in bytes.
        max_records (int): Maximum number of records the buffer can hold before triggering an automatic flush.
        pk_field_name (str): The name of the field that acts as the primary key within the records.
        schema (RelationSchema): Schema definition including the primary key and columns.
        column_names (Tuple[str]): Sorted tuple of column names derived from the schema for consistent record serialization.
    """

    def __init__(self, schema: RelationSchema, max_records: int = 10000):
        """
        Initializes the MemoryTable with an empty buffer and specified configurations based on the provided schema.

        Parameters:
            schema (RelationSchema): The schema defining the structure of records and the primary key.
            max_records (int, optional): The maximum number of records to store before flushing to durable storage.
                Defaults to 10,000.
        """
        self.buffer = {}
        self.buffer_size = 0
        self.max_records = max_records
        self.pk_field_name = schema.primary_key
        self.schema = schema
        self.column_names = tuple(sorted(schema.column_names))

    def append(self, record: Dict):
        """
        Appends a record to the MemoryTable. If the primary key of the record is already present, the existing
        record is overwritten. The function automatically triggers a flush if the number of records exceeds
        the configured maximum.

        Parameters:
            record (Dict): A dictionary representing the record to append, must include the primary key.

        Raises:
            ValueError: If the primary key is missing from the record.
        """

        def serialize(value):
            if isinstance(value, numpy.datetime64):
                if numpy.isnat(value):
                    return None
                return ("__datetime__", value.astype("datetime64[s]").astype("int"))
            if isinstance(value, datetime.datetime):
                return ("__datetime__", value.timestamp())
            if isinstance(value, numpy.ndarray):
                return list(value)
            return str(value)

        primary_key = record.get(self.pk_field_name)
        if primary_key is None:
            raise ValueError("Primary Key cannot be missing or have None value")

        serialized_record = tuple(record.get(field) for field in self.column_names)
        record_bytes = ormsgpack.packb(
            serialized_record, option=ormsgpack.OPT_SERIALIZE_NUMPY, default=serialize
        )

        # Adjust buffer size for overwritten records
        if primary_key in self.buffer:
            self.buffer_size -= len(self.buffer[primary_key][1])
        self.buffer[primary_key] = (time.time_ns(), record_bytes)
        self.buffer_size += len(record_bytes)

        # Trigger flush if needed
        if len(self.buffer) >= self.max_records:
            raise MaximumRecordsExceeded(self)

    def flush(self):
        """
        Flushes the current in-memory buffer to durable storage. This operation clears the buffer and resets
        the buffer size counter.
        """
        self._persist_to_storage()
        self.buffer.clear()
        self.buffer_size = 0

    def _persist_to_storage(self):
        """
        Internal method to persist buffered log entries to durable storage.
        This is a placeholder that should be overridden with actual storage logic.
        """
        pass  # Implementation of persistence logic

    def _get(self, pk: Any) -> Optional[Tuple[int, bytes]]:
        """
        Retrieves the record associated with the given primary key.

        Parameters:
            pk (Any): The primary key of the record to retrieve.

        Returns:
            Optional[Tuple[int, bytes]]: The tuple containing the timestamp and record bytes, or None if not found.
        """
        return self.buffer.get(pk)

    def __repr__(self):
        return f"<MemoryTable rows={len(self.buffer)}, buffer_size={self.buffer_size} bytes>"
