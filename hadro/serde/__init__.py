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
from hadro.serde.data_and_key_block import create_blocks
from hadro.serde.section_header import SectionBlockTypes
from hadro.serde.section_header import SectionHeader


def commit_sstable(memory_table, location):

    file = bytearray()
    file += HEADER

    data, keys = create_blocks(memory_table)
    data_header = SectionHeader(SectionBlockTypes.DATA_BLOCK, len(data), flags=1).to_bytes()

    file += data_header
    file += data

    file += b"SCHEMA"

    key_header = SectionHeader(SectionBlockTypes.KEY_BLOCK, len(keys), flags=0).to_bytes()
    file += key_header
    file += keys

    print(len(file))
