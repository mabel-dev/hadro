import struct
import time
import lz4.frame
import zstandard as zstd
from orso.tools import monitor

@monitor()
def create_blocks(memory_table, compression_type:str='zstd') -> tuple:
    sorted_keys = sorted(memory_table.buffer.keys())
    data_block = bytearray()
    key_block = bytearray()

    t = time.monotonic_ns()

    # Write value block to the buffer
    for key in sorted_keys:
        timestamp_ns, record = memory_table.buffer[key]
        offset = len(data_block)
        data_block += len(record).to_bytes(4, 'little') + record  # Ensure byte order is explicit
        length = len(record) + 4
        key_block += struct.pack("qqII", int(key * 100000), timestamp_ns, offset, length)

    print(time.monotonic_ns() - t, "time building block")

    t = time.monotonic_ns()

    if compression_type == 'lz4':
        compressed_data_block = lz4.frame.compress(data_block)
    elif compression_type == 'zstd':
        cctx = zstd.ZstdCompressor()
        compressed_data_block = cctx.compress(data_block)
    else:
        raise ValueError(f"Unsupported compression type: {compression_type}")

    print(time.monotonic_ns() - t, f"time {compression_type} compression block")

    print("compressed", len(data_block), "to", len(compressed_data_block))

    return compressed_data_block, key_block
