import pyarrow.fs
import pyarrow.parquet as pq
import pytest
from botocore.exceptions import ClientError


def test_get_object(s3, data_dir):
    response = s3.get_object(Bucket="astronauts", Key="astronauts.parquet")
    assert response["Body"].read() == (data_dir / "astronauts/astronauts.parquet").read_bytes()
    assert response["AcceptRanges"] == "bytes"
    assert response["ETag"].startswith('"')


def test_get_nested_key_with_special_characters(s3):
    body = s3.get_object(Bucket="nested", Key="space and %25.txt")["Body"].read()
    assert body == b"contents of space and %25.txt"
    assert (
        s3.get_object(Bucket="nested", Key="b/c/3.txt")["Body"].read() == b"contents of b/c/3.txt"
    )


def test_content_type(s3):
    assert s3.head_object(Bucket="nested", Key="z.csv")["ContentType"] == "text/csv"


def test_head_object(s3, data_dir):
    response = s3.head_object(Bucket="astronauts", Key="astronauts.parquet")
    assert response["ContentLength"] == (data_dir / "astronauts/astronauts.parquet").stat().st_size
    listed = s3.list_objects_v2(Bucket="astronauts")["Contents"][0]
    assert response["ETag"] == listed["ETag"]


@pytest.mark.parametrize(
    "header, expected, content_range",
    [
        ("bytes=0-4", b"conte", "bytes 0-4/17"),
        ("bytes=12-", b"a.txt", "bytes 12-16/17"),
        ("bytes=-3", b"txt", "bytes 14-16/17"),
        ("bytes=10-1000", b"f a.txt", "bytes 10-16/17"),
    ],
)
def test_range(s3, header, expected, content_range):
    response = s3.get_object(Bucket="nested", Key="a.txt", Range=header)
    assert response["Body"].read() == expected
    assert response["ContentRange"] == content_range
    assert response["ResponseMetadata"]["HTTPStatusCode"] == 206


def test_unsatisfiable_range(s3):
    with pytest.raises(ClientError) as err:
        s3.get_object(Bucket="nested", Key="a.txt", Range="bytes=100-200")
    assert err.value.response["Error"]["Code"] == "InvalidRange"


def test_malformed_range_returns_whole_object(client):
    response = client.get("/nested/a.txt", headers={"Range": "bytes=0-1,4-5"})
    assert response.status_code == 200
    assert response.content == b"contents of a.txt"


def test_missing_object_and_bucket(s3):
    with pytest.raises(ClientError) as err:
        s3.get_object(Bucket="nested", Key="nope.txt")
    assert err.value.response["Error"]["Code"] == "NoSuchKey"
    with pytest.raises(ClientError) as err:
        s3.get_object(Bucket="missing", Key="nope.txt")
    assert err.value.response["Error"]["Code"] == "NoSuchBucket"
    with pytest.raises(ClientError) as err:
        s3.head_object(Bucket="nested", Key="nope.txt")
    assert err.value.response["Error"]["Code"] == "404"


def test_directories_are_not_objects(s3):
    with pytest.raises(ClientError):
        s3.get_object(Bucket="nested", Key="b")


@pytest.mark.parametrize(
    "path",
    [
        "/nested/..%2F..%2Fsecret.txt",
        "/nested/../secret.txt",
        "/nested/b/..%2F..%2F..%2Fsecret.txt",
        "/..%2Fsecret.txt/x",
        "/nested/%2Fetc%2Fpasswd",
    ],
)
def test_path_traversal_is_blocked(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert b"outside any bucket" not in response.content


def test_listing_prefix_traversal_is_blocked(client):
    response = client.get("/nested", params={"prefix": "../"})
    assert response.status_code == 200
    assert b"secret" not in response.content


def test_writes_are_rejected(s3, client):
    with pytest.raises(ClientError) as err:
        s3.put_object(Bucket="nested", Key="new.txt", Body=b"x")
    assert err.value.response["Error"]["Code"] == "MethodNotAllowed"
    with pytest.raises(ClientError) as err:
        s3.delete_object(Bucket="nested", Key="a.txt")
    assert err.value.response["Error"]["Code"] == "MethodNotAllowed"
    with pytest.raises(ClientError) as err:
        s3.create_bucket(Bucket="brand-new")
    assert err.value.response["Error"]["Code"] == "MethodNotAllowed"
    with pytest.raises(ClientError) as err:
        s3.delete_objects(Bucket="nested", Delete={"Objects": [{"Key": "a.txt"}]})
    assert err.value.response["Error"]["Code"] == "MethodNotAllowed"
    assert client.post("/nested/a.txt").status_code == 405


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_pyarrow_reads_parquet_over_s3(server):
    """pyarrow (and so Opteryx) reads Parquet with ranged GETs."""
    host = server.endpoint.removeprefix("http://")
    filesystem = pyarrow.fs.S3FileSystem(
        endpoint_override=host, scheme="http", access_key="a", secret_key="b", region="eu-west-2"
    )
    table = pq.read_table("astronauts/astronauts.parquet", filesystem=filesystem, columns=["name"])
    assert table.num_rows == 357
    selector = pyarrow.fs.FileSelector("nested/b", recursive=True)
    paths = sorted(info.path for info in filesystem.get_file_info(selector) if info.is_file)
    assert paths == ["nested/b/1.txt", "nested/b/2.txt", "nested/b/c/3.txt"]


def test_minio_client(server, data_dir):
    from minio import Minio

    client = Minio(
        server.endpoint.removeprefix("http://"), access_key="a", secret_key="b", secure=False
    )
    names = [o.object_name for o in client.list_objects("nested", recursive=True)]
    assert "b/c/3.txt" in names
    response = client.get_object("astronauts", "astronauts.parquet")
    try:
        assert response.read() == (data_dir / "astronauts/astronauts.parquet").read_bytes()
    finally:
        response.close()
        response.release_conn()
