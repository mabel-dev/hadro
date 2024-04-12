import io
import struct
from typing import Any
from typing import Dict

import lz4.frame
from ormsgpack import OPT_SERIALIZE_NUMPY
from ormsgpack import packb

from hadro.__version__ import HEADER


def _serialize_value(value: Any) -> bytes:
    return str(value).encode()


def write(memory_table):
    buffer = io.BytesIO()
    # Sort the keys (primary key and timestamp) for ordering in the SSTable
    sorted_keys = sorted(memory_table.buffer.keys())
    offsets_lengths = []

    # Write value block to the buffer
    for key in sorted_keys:
        timestamp_ns, record = memory_table.buffer[key]
        serialized = packb(record, option=OPT_SERIALIZE_NUMPY, default=_serialize_value)
        offset = buffer.tell()
        buffer.write(serialized)
        length = len(serialized)
        offsets_lengths.append((key, timestamp_ns, offset, length))

    compressed_batch = lz4.frame.compress(buffer.getvalue())

    # Prepare and write the key block to the buffer
    key_block_start = buffer.tell()
    for pk, timestamp_ns, offset, length in offsets_lengths:
        # Adjust struct packing as necessary for your key types
        buffer.write(struct.pack("qqII", pk, timestamp_ns, offset, length))

    # Store the start of the key block for later retrieval
    buffer.write(struct.pack("q", key_block_start))

    return buffer
