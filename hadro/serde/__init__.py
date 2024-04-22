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
from ormsgpack import packb

from hadro.__version__ import HEADER
from hadro.serde.data_and_key_block import create_blocks
from hadro.serde.section_header import SectionBlockTypes
from hadro.serde.section_header import SectionHeader

SECTION_HEADER_LEN:int = 9



def commit_sstable(memory_table, location):

    """
    DATA_BLOCK: int = 1
    SCHEMA_BLOCK: int = 2
    KEY_BLOCK: int = 3
    *STATISTICS_BLOCK: int = 4
    *INDEX_BLOCK: int = 5
    BLOCK_TABLE: int = 6
    *METADATA_BLOCK: int = 7
    """

    file = bytearray()
    file += HEADER

    data, keys = create_blocks(memory_table, compression_type="zstd")
    data_header = SectionHeader(SectionBlockTypes.DATA_BLOCK, len(data), flags=1).to_bytes()
    file += data_header
    file += data

    schema = packb(memory_table.schema.to_dict())
    schema_header = SectionHeader(SectionBlockTypes.SCHEMA_BLOCK, len(schema), flags=0).to_bytes()
    file += schema_header
    file += schema

    key_header = SectionHeader(SectionBlockTypes.KEY_BLOCK, len(keys), flags=0).to_bytes()
    file += key_header
    file += keys

    if isinstance(location, str):
        with open(location, "wb") as file_:
            file_.write(file)
    else:
        io.seek(0, 0)
        io.write(file)

    return len(file)    


import os

def read(file_path_or_handle, columns, filters):
    """
    Opens and reads the contents of a file to verify if it has the correct HEADER
    at the beginning and checks if the end matches the HEADER. This function consumes 
    the file as it reads, making it more memory efficient.
    
    Parameters:
        file_path_or_handle: str or file object
            The path to the file or an already opened file object in binary mode.
    
    Returns:
        None
    """
    close_file = False
    if isinstance(file_path_or_handle, str):
        file = open(file_path_or_handle, "rb")
        close_file = True  # We need to close the file later because we opened it here
    else:
        file = file_path_or_handle  # Assume it's an already opened file object

    try:
        file_size = os.fstat(file.fileno()).st_size  # Get file size
        header = file.read(len(HEADER))
        if header != HEADER:
            print("NOT A HADRO FILE")
            return
        
        print("Valid HADRO file header")

        while True:
            next_position = file.tell()
            if next_position > file_size - len(HEADER):
                # When we're within len(HEADER) bytes of the file end
                break
            section_header_bytes = file.read(SECTION_HEADER_LEN)
            if len(section_header_bytes) < SECTION_HEADER_LEN:
                # Incomplete section header: might be end of file or a corrupt file
                break
            section_header = SectionHeader.from_bytes(section_header_bytes)
            section = file.read(section_header.section_length)
            if len(section) < section_header.section_length:
                # Incomplete section: might be end of file or a corrupt file
                break

            if section_header.section_type == SectionBlockTypes.DATA_BLOCK:
                data_block = section
            if section_header.section_type == SectionBlockTypes.SCHEMA_BLOCK:
                schema_block = section
            if section_header.section_type == SectionBlockTypes.KEY_BLOCK:
                key_block = section

        # Check the last len(HEADER) bytes
        file.seek(-len(HEADER), os.SEEK_END)
        trailer = file.read(len(HEADER))
        if trailer == HEADER:
            print("Valid HADRO file trailer")
        else:
            print("Invalid or missing trailer")

    finally:
        if close_file:
            file.close()





    