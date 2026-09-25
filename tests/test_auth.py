from datetime import datetime, timedelta, timezone

import pytest
import requests
from botocore.exceptions import ClientError

import hadro
from hadro import auth
from hadro.errors import S3Error

from .conftest import make_s3


@pytest.fixture(scope="module")
def secured(data_dir):
    with hadro.Server(data=str(data_dir), access_key="AKIDTEST", secret_key="s3cr3t") as server:
        yield server


def test_signed_requests_succeed(secured):
    s3 = make_s3(secured.endpoint, "AKIDTEST", "s3cr3t")
    assert "nested" in [b["Name"] for b in s3.list_buckets()["Buckets"]]
    keys = [
        o["Key"]
        for o in s3.list_objects_v2(Bucket="nested", Prefix="b/", Delimiter="/")["Contents"]
    ]
    assert keys == ["b/1.txt", "b/2.txt"]
    body = s3.get_object(Bucket="nested", Key="space and %25.txt")["Body"].read()
    assert body == b"contents of space and %25.txt"
    response = s3.select_object_content(
        Bucket="astronauts",
        Key="astronauts.parquet",
        Expression="SELECT name FROM S3Object LIMIT 1",
        ExpressionType="SQL",
        InputSerialization={"Parquet": {}},
        OutputSerialization={"JSON": {}},
    )
    assert any("Records" in event for event in response["Payload"])


def test_wrong_secret(secured):
    s3 = make_s3(secured.endpoint, "AKIDTEST", "wrong")
    with pytest.raises(ClientError) as err:
        s3.list_buckets()
    assert err.value.response["Error"]["Code"] == "SignatureDoesNotMatch"


def test_unknown_access_key(secured):
    s3 = make_s3(secured.endpoint, "SOMEONE", "s3cr3t")
    with pytest.raises(ClientError) as err:
        s3.get_object(Bucket="nested", Key="a.txt")
    assert err.value.response["Error"]["Code"] == "InvalidAccessKeyId"


def test_anonymous_rejected(secured):
    response = requests.get(f"{secured.endpoint}/nested/a.txt")
    assert response.status_code == 403
    assert b"<Code>AccessDenied</Code>" in response.content
    assert requests.get(f"{secured.endpoint}/health").status_code == 200


def test_presigned_url(secured):
    s3 = make_s3(secured.endpoint, "AKIDTEST", "s3cr3t")
    url = s3.generate_presigned_url(
        "get_object", Params={"Bucket": "nested", "Key": "b/c/3.txt"}, ExpiresIn=60
    )
    response = requests.get(url)
    assert response.status_code == 200 and response.content == b"contents of b/c/3.txt"

    tampered = url.replace("b/c/3.txt", "b/1.txt")
    assert requests.get(tampered).status_code == 403


def test_presigned_url_expiry():
    signed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    query = (
        "X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIDTEST%2F20260101%2Feu-west-2%2Fs3"
        "%2Faws4_request&X-Amz-Date=20260101T000000Z&X-Amz-Expires=60"
        "&X-Amz-SignedHeaders=host&X-Amz-Signature=00"
    )
    with pytest.raises(S3Error) as err:
        auth.verify(
            method="GET",
            raw_path="/b/k",
            query_string=query,
            headers={"host": "localhost"},
            body=b"",
            access_key="AKIDTEST",
            secret_key="s3cr3t",
            now=signed_at + timedelta(minutes=5),
        )
    assert err.value.message == "Request has expired"


def test_payload_hash_mismatch():
    with pytest.raises(S3Error) as err:
        auth.verify(
            method="POST",
            raw_path="/b/k",
            query_string="select=",
            headers={
                "authorization": "AWS4-HMAC-SHA256 Credential=AKIDTEST/20260101/eu-west-2/s3/"
                "aws4_request, SignedHeaders=host, Signature=00",
                "x-amz-content-sha256": "0" * 64,
            },
            body=b"<xml/>",
            access_key="AKIDTEST",
            secret_key="s3cr3t",
        )
    assert err.value.code == "XAmzContentSHA256Mismatch"


def test_canonical_query_is_sorted_and_encoded():
    pairs = [("prefix", "a b/"), ("delimiter", "/"), ("list-type", "2")]
    assert auth.canonical_query(pairs) == "delimiter=%2F&list-type=2&prefix=a%20b%2F"
