# S1

Implementing only a little bit of S3

## Overview

S1 is a lightweight S3-compatible API implementation that provides core S3 services for reading data from Google Cloud Storage (GCS). It implements the following S3 APIs:

### Implemented Features

1. **GetBucketLocation** - Returns the region where the bucket resides
2. **ListObjects** - Lists objects in a bucket with support for filtering
3. **GetObject** - Retrieves objects from a bucket
4. **SelectObjectContent (S3 Select)** - Enables SQL queries on S3 objects for data filtering and transformation

## API Endpoints

### 1. GetBucketLocation
```
GET /{bucket}?location
```
Returns the AWS region for the bucket (always returns `eu-west-2`).

### 2. ListObjects
```
GET /{bucket}?delimiter={delimiter}&prefix={prefix}&max-keys={max-keys}&marker={marker}
```
Lists objects in a bucket. Supports query parameters:
- `prefix` - Limits response to keys that begin with the specified prefix
- `delimiter` - Character used to group keys
- `max-keys` - Maximum number of keys to return (default: 1000)
- `marker` - Key to start with when listing objects

### 3. GetObject
```
GET /{bucket}/{object}
```
Retrieves an object from the bucket.

### 4. SelectObjectContent (S3 Select)
```
POST /{bucket}/{object}?select&select-type=2
```
Performs SQL queries on objects stored in S3. The request body should contain XML with:
- SQL expression
- Input serialization format (Parquet only)
- Output serialization format (CSV or JSON)

**Note**: The SQL API only supports Parquet files. For accessing other file types (CSV, JSON, etc.), use the GetObject endpoint.

#### Example Request Body:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<SelectObjectContentRequest xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
    <Expression>SELECT * FROM S3Object WHERE price > 100</Expression>
    <ExpressionType>SQL</ExpressionType>
    <InputSerialization>
        <Parquet/>
    </InputSerialization>
    <OutputSerialization>
        <JSON/>
    </OutputSerialization>
</SelectObjectContentRequest>
```

## Supported S3 Select Features

- **Input Formats**: Parquet only
- **Output Formats**: CSV, JSON
- **SQL Operations**: 
  - SELECT with column specification or wildcard (*)
  - Basic WHERE clause filtering
  - Queries against S3Object alias

**Important**: The SQL API (SelectObjectContent) only supports Parquet files. For other file formats like CSV or JSON, use the GetObject API for blob access.

## Architecture

The implementation uses:
- **FastAPI** for the web framework
- **Storage abstraction layer** supporting both Google Cloud Storage and local filesystem
- **LRU caching** for improved read performance using Python's `functools.lru_cache`
- **XML parsing** for S3 Select request handling
- **Parquet support** for SQL queries (other formats available via GetObject)

## Running the Service

Requires Python 3.10+.

```bash
pip install -e .            # runtime dependencies
pip install -e '.[test]'    # plus test dependencies
python src/main.py
```

Or use `make run`, which creates a `.venv`, installs the package and starts the service with the local storage backend.

The service will start on port 8080 (or the port specified in the `PORT` environment variable).

## Storage Backend

S1 supports two storage backends:

### Google Cloud Storage (GCS)
The default backend uses Google Cloud Storage (GCS). When `STORAGE_EMULATOR_HOST` environment variable is set, it connects to a storage emulator for testing purposes.

### Local Filesystem
S1 can also use the local filesystem as a storage backend, which is useful for testing or development environments.

### Configuration

The storage backend is configured using environment variables:

- **`STORAGE_BACKEND`** - Set to `gcs` (default) or `local` to choose the backend
- **`STORAGE_CACHE_SIZE`** - LRU cache size for blob content (default: 128)
- **`LOCAL_STORAGE_PATH`** - Base path for local filesystem storage (default: `/data`)
- **`GCS_PROJECT`** - GCS project name (default: `PROJECT`)
- **`STORAGE_EMULATOR_HOST`** - GCS emulator host for testing

### LRU Caching

S1 implements LRU (Least Recently Used) caching for blob content reads. This significantly improves performance when the same objects are accessed multiple times. The cache size can be configured using the `STORAGE_CACHE_SIZE` environment variable.

The caching layer operates transparently for both GCS and local filesystem backends, making S1 an effective caching layer for systems like Opteryx.
