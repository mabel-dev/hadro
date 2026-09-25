<div align="center">

![Hadro](https://raw.githubusercontent.com/mabel-dev/hadro/main/hadro.png)

</div>

# hadro

A small, **read-only**, S3-compatible server. Point it at a local directory, or at
Google Cloud Storage, and use any S3 client (boto3, MinIO, pyarrow, Opteryx, the AWS CLI)
to list, download and query the data, including **S3 Select** over Parquet.

It's useful for:

- **Tests:** give code that reads from S3 a real endpoint with no AWS account or Docker.
- **Local development:** serve a folder of Parquet/CSV/JSON files as buckets.
- **An S3 front-end for GCS:** expose GCS buckets to S3-only tools, with an in-memory cache.

hadro evolved from [S1](https://github.com/mabel-dev/s1) and the `cache.opteryx.app` Cloud Run
service. Releases up to 0.5.0a6 were an unrelated storage engine (hadrodb), which is still in
this repository's history.

## Install

```bash
pip install hadro          # local directories
pip install 'hadro[gcs]'   # plus Google Cloud Storage
```

## Run

```bash
hadro ./data               # every sub-directory of ./data is a bucket
```

```text
data/
├── astronauts/            -> s3://astronauts
│   └── astronauts.parquet -> s3://astronauts/astronauts.parquet
└── planets/
    └── planets.parquet
```

Then use it like any S3 endpoint (hadro only supports path-style addressing):

```python
import boto3
from botocore.config import Config

s3 = boto3.client(
    "s3",
    endpoint_url="http://127.0.0.1:8080",
    aws_access_key_id="anything",
    aws_secret_access_key="anything",
    region_name="eu-west-2",
    config=Config(s3={"addressing_style": "path"}),
)
s3.list_objects_v2(Bucket="astronauts")
```

```python
import pyarrow.fs, pyarrow.parquet as pq

fs = pyarrow.fs.S3FileSystem(endpoint_override="127.0.0.1:8080", scheme="http", anonymous=True)
pq.read_table("astronauts/astronauts.parquet", filesystem=fs)
```

### In tests

`hadro.Server` runs hadro in a background thread on a free port:

```python
import hadro
import pytest

@pytest.fixture(scope="session")
def s3_endpoint():
    with hadro.Server(data="tests/data") as server:
        yield server.endpoint   # e.g. http://127.0.0.1:53817
```

`hadro.create_app(config)` returns the FastAPI app if you would rather use
`fastapi.testclient.TestClient` or mount it yourself.

### Serving GCS

```bash
hadro --backend gcs --gcs-project my-project
```

This uses Application Default Credentials, or `STORAGE_EMULATOR_HOST` for a GCS emulator.
Objects up to a quarter of the cache size are kept in memory (256MB by default; see
`--cache-mb` and `--cache-ttl`).

### Docker / Cloud Run

```bash
docker build -t hadro .
docker run -p 8080:8080 -v "$PWD/data:/data" hadro
docker run -p 8080:8080 -e HADRO_BACKEND=gcs hadro
```

## Configuration

Settings can be given as CLI flags, as `HADRO_*` environment variables, or as fields
of `hadro.Config`.

| Flag | Environment | Default | |
| --- | --- | --- | --- |
| `DATA` (positional) | `HADRO_DATA` | `data` | Directory to serve (local backend) |
| `--backend` | `HADRO_BACKEND` | `local` | `local` or `gcs` |
| `--gcs-project` | `HADRO_GCS_PROJECT` | | Project used to list buckets |
| `--host` | `HADRO_HOST` | `127.0.0.1` | Interface to bind |
| `--port` | `HADRO_PORT`, then `PORT` | `8080` | |
| `--region` | `HADRO_REGION` | `eu-west-2` | Region reported by GetBucketLocation |
| `--cache-mb` | `HADRO_CACHE_MB` | 256 (gcs), 0 (local) | In-memory object cache |
| `--cache-ttl` | `HADRO_CACHE_TTL` | `300` | Seconds before cached objects are refetched (0 = never) |
| `--access-key` | `HADRO_ACCESS_KEY` | | Require SigV4-signed requests... |
| `--secret-key` | `HADRO_SECRET_KEY` | | ...with this key pair |

With no keys set, hadro accepts any request, signed or not. With keys set, it checks
SigV4 signatures in the `Authorization` header and in presigned URLs.

## S3 API coverage

| Operation | |
| --- | --- |
| ListBuckets | |
| HeadBucket, GetBucketLocation | |
| ListObjects, ListObjectsV2 | prefix, delimiter / CommonPrefixes, pagination, `encoding-type=url` |
| GetObject, HeadObject | single `Range` requests, `ETag`, `Last-Modified`, `Content-Type` |
| SelectObjectContent | Parquet input only; see below |
| Anything that writes | Rejected with `405 MethodNotAllowed` |
| Other sub-resources (`?acl`, `?versioning`...) | `501 NotImplemented` |

Local-backend ETags are derived from each file's size and modification time rather than
an MD5 of its contents, so they are stable and change whenever the file does.

## S3 Select

```python
response = s3.select_object_content(
    Bucket="astronauts",
    Key="astronauts.parquet",
    Expression="SELECT name, missions FROM S3Object s WHERE s.space_flights > 5 LIMIT 10",
    ExpressionType="SQL",
    InputSerialization={"Parquet": {}},
    OutputSerialization={"JSON": {}},
)
for event in response["Payload"]:
    if "Records" in event:
        print(event["Records"]["Payload"].decode())
```

Supported SQL:

```sql
SELECT * | column [[AS] alias], ...
FROM S3Object [[AS] alias]
[WHERE condition]
[LIMIT n]
```

- Comparisons `= != <> < <= > >=` and `IS [NOT] NULL`, `[NOT] IN (...)`,
  `[NOT] BETWEEN ... AND ...`, `[NOT] LIKE`, combined with `AND`, `OR`, `NOT` and parentheses.
- Literals are converted to the column's type, so `birth_date < '1960-01-01'` works on
  date and timestamp columns.
- Unquoted column names are case-insensitive; `"quoted"` names are exact.
- Filters and column selection are pushed down into the Parquet reader.
- Aggregates, functions, `GROUP BY` and `ORDER BY` are not supported.

Output can be JSON Lines or CSV (with custom delimiters and quoting), or **Parquet**.
Parquet output is a hadro extension: send `<OutputSerialization><Parquet/></OutputSerialization>`,
optionally with `CompressionAlgorithm`, `CompressionLevel` and `WriteStatistics`.
`SELECT *` with Parquet output returns the original file untouched.

Results are streamed in 10,000-row `Records` events, followed by `Stats` and `End`.

## Development

```bash
make install   # creates .venv with test and gcs extras
make test
make lint
make run       # serves ./data on port 8080
```

## Migrating from S1 / cache.opteryx.app

- The package is `hadro`; start it with `hadro` or `python -m hadro` rather than `python src/main.py`.
- Environment variables now have a `HADRO_` prefix: `STORAGE_BACKEND` → `HADRO_BACKEND`,
  `LOCAL_STORAGE_PATH` → `HADRO_DATA`, `GCS_PROJECT` → `HADRO_GCS_PROJECT`,
  `S1_ACCESS_KEY`/`S1_SECRET_KEY` → `HADRO_ACCESS_KEY`/`HADRO_SECRET_KEY`.
  `STORAGE_CACHE_SIZE` (a count of objects) is replaced by `HADRO_CACHE_MB`.
- The default backend is now `local`, and the default host is `127.0.0.1`.
- Errors are S3 XML documents (`NoSuchKey`, `NoSuchBucket`, ...) instead of plain text.

## License

Apache 2.0; see [LICENSE](LICENSE).
