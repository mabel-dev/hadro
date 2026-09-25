from xml.etree import ElementTree as ET

import pytest
from botocore.exceptions import ClientError

NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def _all_keys(s3, **kwargs):
    keys, prefixes = [], []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket="nested", **kwargs):
        keys += [o["Key"] for o in page.get("Contents", [])]
        prefixes += [p["Prefix"] for p in page.get("CommonPrefixes", [])]
    return keys, prefixes


def test_list_buckets(s3):
    names = [b["Name"] for b in s3.list_buckets()["Buckets"]]
    assert names == ["astronauts", "empty", "events", "nested", "planets", "tweets"]


def test_head_bucket(s3):
    s3.head_bucket(Bucket="nested")
    with pytest.raises(ClientError) as err:
        s3.head_bucket(Bucket="missing")
    assert err.value.response["Error"]["Code"] == "404"


def test_bucket_location(s3):
    assert s3.get_bucket_location(Bucket="nested")["LocationConstraint"] == "eu-west-2"


def test_list_recursive(s3):
    keys, prefixes = _all_keys(s3)
    assert keys == [
        "a.txt",
        "b/1.txt",
        "b/2.txt",
        "b/c/3.txt",
        "d/4.txt",
        "d/e/5.txt",
        "space and %25.txt",
        "z.csv",
    ]
    assert prefixes == []


def test_list_with_delimiter(s3):
    keys, prefixes = _all_keys(s3, Delimiter="/")
    assert keys == ["a.txt", "space and %25.txt", "z.csv"]
    assert prefixes == ["b/", "d/"]

    keys, prefixes = _all_keys(s3, Delimiter="/", Prefix="b/")
    assert keys == ["b/1.txt", "b/2.txt"]
    assert prefixes == ["b/c/"]


def test_list_partial_prefix(s3):
    keys, _ = _all_keys(s3, Prefix="b/c")
    assert keys == ["b/c/3.txt"]
    keys, _ = _all_keys(s3, Prefix="nothing/")
    assert keys == []


@pytest.mark.parametrize("page_size", [1, 2, 3])
def test_pagination_v2_with_delimiter(s3, page_size):
    keys, prefixes = _all_keys(s3, Delimiter="/", PaginationConfig={"PageSize": page_size})
    assert keys == ["a.txt", "space and %25.txt", "z.csv"]
    assert prefixes == ["b/", "d/"]


@pytest.mark.parametrize("page_size", [1, 3])
def test_pagination_v1(s3, page_size):
    keys = []
    for page in s3.get_paginator("list_objects").paginate(
        Bucket="nested", PaginationConfig={"PageSize": page_size}
    ):
        keys += [o["Key"] for o in page.get("Contents", [])]
    assert len(keys) == 8 and keys == sorted(keys)


def test_start_after(s3):
    response = s3.list_objects_v2(Bucket="nested", StartAfter="d/4.txt")
    assert [o["Key"] for o in response["Contents"]] == ["d/e/5.txt", "space and %25.txt", "z.csv"]


def test_list_object_metadata(s3):
    item = s3.list_objects_v2(Bucket="nested", Prefix="a.txt")["Contents"][0]
    assert item["Size"] == len("contents of a.txt")
    assert item["ETag"].startswith('"') and len(item["ETag"]) == 34
    assert item["LastModified"].tzinfo is not None


def test_list_empty_and_missing_bucket(s3):
    assert s3.list_objects_v2(Bucket="empty")["KeyCount"] == 0
    with pytest.raises(ClientError) as err:
        s3.list_objects_v2(Bucket="missing")
    assert err.value.response["Error"]["Code"] == "NoSuchBucket"


def test_xml_is_escaped(client, data_dir):
    (data_dir / "nested" / "b" / "<&>.txt").write_text("x")
    try:
        response = client.get("/nested", params={"prefix": "b/<"})
        root = ET.fromstring(response.content)
        assert root.find(f"{NS}Contents/{NS}Key").text == "b/<&>.txt"
    finally:
        (data_dir / "nested" / "b" / "<&>.txt").unlink()


def test_url_encoding_type(client):
    response = client.get("/nested", params={"prefix": "space", "encoding-type": "url"})
    root = ET.fromstring(response.content)
    assert root.find(f"{NS}Contents/{NS}Key").text == "space%20and%20%2525.txt"


def test_bad_arguments(client):
    assert client.get("/nested", params={"max-keys": "x"}).status_code == 400
    response = client.get("/nested", params={"list-type": "2", "continuation-token": "!!"})
    assert response.status_code == 400


def test_unsupported_subresource(client):
    response = client.get("/nested", params={"versioning": ""})
    assert response.status_code == 501
    assert b"<Code>NotImplemented</Code>" in response.content
