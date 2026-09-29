<div align="center">

![Hadro](https://raw.githubusercontent.com/mabel-dev/hadro/main/hadro.png)

</div>

# hadro

A small, **read-only**, S3-compatible server. Point it at a local directory, or at
Google Cloud Storage, and use any S3 client (boto3, MinIO, Opteryx, the AWS CLI) to list,
download and query the data, including **S3 Select** over Parquet, JSON Lines and CSV.

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

hadro needs Python 3.11+ on Linux (x86-64 or aarch64, glibc 2.34+) or macOS on Apple silicon,
the platforms [rugo](https://pypi.org/project/rugo/) publishes wheels for. It does not use
pyarrow, pandas or numpy.

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
| `--tls-cert` | `HADRO_TLS_CERT` | | Serve HTTPS with this PEM certificate... |
| `--tls-key` | `HADRO_TLS_KEY` | | ...and this PEM private key (see [HTTPS](#https)) |
| `--access-key` | `HADRO_ACCESS_KEY` | | Require SigV4-signed requests... |
| `--secret-key` | `HADRO_SECRET_KEY` | | ...with this key pair |

With no keys set, hadro accepts any request, signed or not. With keys set, it checks
SigV4 signatures in the `Authorization` header and in presigned URLs.

## HTTPS

hadro serves plain HTTP unless given a certificate and key, which is enough to test
clients that insist on `https://` endpoints. Give both, as PEM files:

```bash
hadro ./data --tls-cert cert.pem --tls-key key.pem     # https://127.0.0.1:8080
```

For local use, make a self-signed certificate. The `subjectAltName` matters: clients
check the host they connect to against it, so list every name or address you will use.

```bash
openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 30 \
    -keyout key.pem -out cert.pem -subj "/CN=localhost" \
    -addext "subjectAltName=IP:127.0.0.1,DNS:localhost"
```

A self-signed certificate is its own authority, so clients must be told to trust it
rather than to skip verification:

| Client | How to trust `cert.pem` |
| --- | --- |
| Anything built on OpenSSL or libcurl (curl, Opteryx) | `SSL_CERT_FILE=cert.pem` |
| Python `requests` | `REQUESTS_CA_BUNDLE=cert.pem` |
| boto3 / AWS CLI | `AWS_CA_BUNDLE=cert.pem`, or `verify="cert.pem"` on the client |
| MinIO client | pass `http_client=urllib3.PoolManager(cert_reqs="CERT_REQUIRED", ca_certs="cert.pem")` |

In tests, `hadro.Server(data=..., tls_cert="cert.pem", tls_key="key.pem")` serves HTTPS and
`server.endpoint` starts with `https://`.

The certificate is used as given; hadro does not generate one, renew one, or reload it
while running. Use a real certificate, or terminate TLS in front of hadro, for anything
that is not local development.

## Behaving like a remote store

By default hadro answers instantly, which is nothing like GCS or S3. These options make
it behave like a distant blob store, for benchmarking and for reproducing timeouts and
retries. Each is off at `0`, and with all of them off there is no overhead.

| Flag | Environment | `hadro.Config` | |
| --- | --- | --- | --- |
| `--latency-ms` | `HADRO_LATENCY_MS` | `latency_ms` | Delay before each request is served (the round trip) |
| `--latency-jitter-ms` | `HADRO_LATENCY_JITTER_MS` | `latency_jitter_ms` | Extra random delay per request, between 0 and this |
| `--bandwidth-mbps` | `HADRO_BANDWIDTH_MBPS` | `bandwidth_mbps` | Cap on each response's transfer rate, in megabits per second |
| `--total-bandwidth-mbps` | `HADRO_TOTAL_BANDWIDTH_MBPS` | `total_bandwidth_mbps` | Cap on all responses together, shared by concurrent requests |
| `--error-rate` | `HADRO_ERROR_RATE` | `error_rate` | Fraction of requests (0 to 1) answered `503 SlowDown` |
| `--fault-seed` | `HADRO_FAULT_SEED` | `fault_seed` | Seed that makes jitter and faults repeatable |

```bash
# ~50ms round trip, 2.5 Mbps per response, 100 Mbps for everything, 2% of requests fail
hadro ./data --latency-ms 50 --bandwidth-mbps 2.5 --total-bandwidth-mbps 100 --error-rate 0.02
```

```python
with hadro.Server(data="tests/data", latency_ms=50, bandwidth_mbps=2.5) as server:
    ...
```

How it behaves:

- **Latency** is applied to every request (GET, HEAD, listings, S3 Select) before it is
  served, including requests that then fail with an error.
- **`--bandwidth-mbps`** limits one response, like a per-stream object-store limit, so N
  concurrent responses move N times that in total. It is per response rather than per TCP
  connection, because the server does not see connections. **`--total-bandwidth-mbps`**
  is the client's whole link: concurrent responses share it. Both can be set; a chunk is
  released when the slower of the two allows it. Bodies are paced in 64 KiB chunks.
- **`--error-rate`** answers with `503 SlowDown` and an S3 XML error before any body
  (HEAD gets the status only). Which requests fail is decided from `--fault-seed` and the
  request's sequence number, so the same seed and request order fail the same way.
- `/health` is never shaped.
- Values below 0, or an error rate above 1, are rejected when the app is created.

Shaping happens after the TLS handshake, so it cannot reproduce handshake failures.

## S3 API coverage

| Operation | |
| --- | --- |
| ListBuckets | |
| HeadBucket, GetBucketLocation | |
| ListObjects, ListObjectsV2 | prefix, delimiter / CommonPrefixes, pagination, `encoding-type=url` |
| GetObject, HeadObject | single `Range` requests, `ETag`, `Last-Modified`, `Content-Type` |
| SelectObjectContent | Parquet, JSON Lines and CSV input; see below |
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
- Aggregates, functions, `GROUP BY` and `ORDER BY` are not supported.

Objects are read with [rugo](https://pypi.org/project/rugo/), the reader Opteryx uses:

| Input | |
| --- | --- |
| Parquet | Column selection; filters pushed into rugo, which skips row groups on footer statistics and filters rows as it decodes |
| JSON Lines | `<JSON><Type>LINES</Type></JSON>`; `CompressionType` `GZIP` or `BZIP2` allowed |
| CSV | `FileHeaderInfo` `USE` (columns by name) or `NONE`/`IGNORE` (`_1`, `_2`, ...); single-character `FieldDelimiter`; `GZIP`/`BZIP2` allowed |

Filtering is native throughout:

- Every top-level `AND`ed condition the input's rugo reader supports is pushed into it:
  comparisons (including `NOT a > 1` and `BETWEEN`) for all three formats, plus `IN`, `NOT IN`
  and `IS [NOT] NULL` for Parquet and JSON Lines. CSV column types aren't known until the file
  is read, so literals are pushed as written; if rugo rejects one as the wrong type, the object
  is read unfiltered and Draken does the filtering.
- Everything else (`OR`, `NOT BETWEEN`, `LIKE`, column-to-column comparisons...) is evaluated
  as Draken boolean vectors, which follow SQL's NULL semantics. `LIKE 'prefix%'` and exact
  patterns use the compare kernels; other patterns are matched in Python on that column only.
- Column types come from the Parquet footer, or are inferred by rugo for JSON Lines and CSV, so
  `WHERE age > 30` compares numbers and `birth_date < '1960-01-01'` compares dates.

The test suite checks every filtering path against a plain-Python reference evaluator.

Output can be JSON Lines or CSV (with custom delimiters and quoting), or **Parquet**.
Parquet output is a hadro extension: send `<OutputSerialization><Parquet/></OutputSerialization>`,
optionally with `<CompressionAlgorithm>` `ZSTD` (the default) or `NONE`.
`SELECT *` with Parquet in and out returns the original file untouched.

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
