import os
import sys
import struct

sys.path.insert(1, os.path.join(sys.path[0], ".."))
sys.path.insert(1, os.path.join(sys.path[0], "../.."))

from hadro.serde.section_header import SectionHeader
from hadro.serde.section_header import SectionBlockTypes
from hadro.serde.section_header import SECTION_FLAG_LZ4_COMPRESSED

def test_section_header_initialization():
    header = SectionHeader(SectionBlockTypes.DATA_BLOCK, 123, SECTION_FLAG_LZ4_COMPRESSED)
    assert header.section_type == SectionBlockTypes.DATA_BLOCK
    assert header.section_length == 123
    assert header.flags == SECTION_FLAG_LZ4_COMPRESSED

def test_to_bytes():
    header = SectionHeader(SectionBlockTypes.SCHEMA_BLOCK, 256, 0)
    expected_bytes = struct.pack("BIB", SectionBlockTypes.SCHEMA_BLOCK.value, 256, 0)
    assert header.to_bytes() == expected_bytes

def test_from_bytes():
    original_bytes = struct.pack("BIB", SectionBlockTypes.KEY_BLOCK.value, 512, SECTION_FLAG_LZ4_COMPRESSED)
    header = SectionHeader.from_bytes(original_bytes)
    assert header.section_type == SectionBlockTypes.KEY_BLOCK
    assert header.section_length == 512
    assert header.flags == SECTION_FLAG_LZ4_COMPRESSED

def test_round_trip_serialization():
    original_header = SectionHeader(SectionBlockTypes.METADATA_BLOCK, 1024, 1)
    serialized = original_header.to_bytes()
    deserialized_header = SectionHeader.from_bytes(serialized)
    assert deserialized_header.section_type == original_header.section_type
    assert deserialized_header.section_length == original_header.section_length
    assert deserialized_header.flags == original_header.flags

if __name__ == "__main__":

    test_section_header_initialization()
    test_to_bytes()
    test_from_bytes()
    test_round_trip_serialization()
