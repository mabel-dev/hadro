"""
| Component    | Description                                                                    |
| ------------ | ------------------------------------------------------------------------------ |
| Magic Bytes  | File type marker and version string                                            |
| Data Block   | LZ4 compressed data block, data stored as packed byte representation of tuples |
| Schema       | Defines the structure of the data including column names and data types        |
| Key Block    | Key table, in key order, holding offset and length of record in Data Block     |
| Statistics   | Data statistics for prefiltering and query planning                            |
| Index Blocks | Blocks holding indexes, expected to be bitmap, sorted lists and vector tables  |
| Block Table  | Location, length, hash and type information for each block in the file         |
| Metadata     | File timestamp                                                                 |
| Magic Bytes  | Confirms the file is complete                                                  |
"""

import io
import struct
from enum import Enum
from typing import Any
from typing import Dict

import lz4.frame
from ormsgpack import OPT_SERIALIZE_NUMPY
from ormsgpack import packb

from hadro.__version__ import HEADER


def magic_bytes(memory_table):
    return HEADER


def key_and_data_block(memory_table):
    return b""


def write(memory_table):
    buffer = io.BytesIO()
    # Sort the keys (primary key and timestamp) for ordering in the SSTable
    sorted_keys = sorted(memory_table.buffer.keys())
    offsets_lengths = []

    # Write value block to the buffer
    for key in sorted_keys:
        timestamp_ns, record = memory_table.buffer[key]
        offset = buffer.tell()
        buffer.write(record)
        length = len(record)
        offsets_lengths.append((key, timestamp_ns, offset, length))

    compressed_batch = lz4.frame.compress(buffer.getvalue())
    print(len(compressed_batch))

    # Prepare and write the key block to the buffer
    key_block_start = buffer.tell()
    for pk, timestamp_ns, offset, length in offsets_lengths:
        # Adjust struct packing as necessary for your key types
        buffer.write(struct.pack("qqII", pk, timestamp_ns, offset, length))

    # Store the start of the key block for later retrieval
    buffer.write(struct.pack("q", key_block_start))

    return buffer
