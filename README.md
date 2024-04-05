<div align="center">

![Hadro](https://raw.githubusercontent.com/mabel-dev/hadro/main/hadro.png)

Hadro is a database storage engine for [Opteryx](https://opteryx.dev).

[![PyPI Latest Release](https://img.shields.io/pypi/v/hadrodb.svg)](https://pypi.org/project/hadro/)
[![codecov](https://codecov.io/gh/mabel-dev/hadro/branch/main/graph/badge.svg?token=nl9JwOVdPs)](https://codecov.io/gh/mabel-dev/hadro)
[![Downloads](https://static.pepy.tech/badge/hadro)](https://pepy.tech/project/hadro)

</div>

## License

[![License](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/mabel-dev/hadro/blob/main/LICENSE)
[![Notices](https://img.shields.io/badge/-Notices-darkgreen.svg)](https://github.com/mabel-dev/hadro/blob/main/NOTICES)

## Status

[![Status](https://img.shields.io/badge/Status-alpha-orange)](https://github.com/mabel-dev/hadro)

Hadro is in alpha. Alpha means different things to different people, to us, being alpha means:

- Interfaces may be significantly changed
- The data file format is unstable
- Expected functionality is missing
- Things that worked yesterday, don't work today
- The results of the system may be unreliable

As such, we really don't recommend using HadroDB anywhere where your data matters.

## File Format

### Magic Bytes

`HADRO`

### Version

`001`

### Header

Section table - Type, Offset, Size, Compression Algo

Record Count

Values Hash

Flags (bytes)
    Value Store Compression Algo (0 = none, 1 = LZ4, 2 = zSTD)

Column Names (not every record has every column)

PK column name

### Statistics

Column, Count, Min, Max, unique values 

### Indexes

Key, Version, Offset, Length

- used for the PK index (mandatory) and other values (optional)

Key is binary encoded and limited to 64 bytes, if the value is longer, it needs to be filtered from the value

OR

number of entries 
value, bitmap

### Values

Entries of MsgPack bytes