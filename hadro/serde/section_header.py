import struct
from enum import Enum

SECTION_FLAG_LZ4_COMPRESSED: int = 1


class SectionBlockTypes(int, Enum):
    DATA_BLOCK: int = 1
    SCHEMA_BLOCK: int = 2
    KEY_BLOCK: int = 3
    STATISTICS_BLOCK: int = 4
    INDEX_BLOCK: int = 5
    BLOCK_TABLE: int = 6
    METADATA_BLOCK: int = 7


class SectionHeader:
    def __init__(self, section_type: SectionBlockTypes, section_length: int, flags: int = 0):
        self.section_type = section_type
        self.section_length = section_length
        self.flags = flags

    def to_bytes(self):
        return struct.pack("BIB", self.section_type.value, self.section_length, self.flags)

    @classmethod
    def from_bytes(cls, bytes_data):
        type_val, len_val, flag_val = struct.unpack("BIB", bytes_data)
        return cls(SectionBlockTypes(type_val), len_val, flag_val)
