import struct
import time

import lz4.frame
from orso.tools import monitor


@monitor()
def create_blocks(memory_table) -> tuple():

    sorted_keys = sorted(memory_table.buffer.keys())
    data_block = bytearray()
    key_block = bytearray()

    t = time.monotonic_ns()

    # Write value block to the buffer
    for key in sorted_keys:
        timestamp_ns, record = memory_table.buffer[key]
        offset = len(data_block)
        data_block += len(record).to_bytes(4) + record
        length = len(record) + 4
        key_block += struct.pack("qqII", int(key * 100000), timestamp_ns, offset, length)

    print(time.monotonic_ns() - t, "time building block")

    t = time.monotonic_ns()
    compressed_data_block = lz4.frame.compress(data_block)
    print(time.monotonic_ns() - t, "time compression block")

    print("compressed", len(data_block), "to", len(compressed_data_block))

    return compressed_data_block, key_block
