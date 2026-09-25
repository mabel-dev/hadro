"""Service- and bucket-level operations: ListBuckets, HeadBucket,
GetBucketLocation, ListObjects and ListObjectsV2."""

from __future__ import annotations

import base64
from urllib.parse import quote

from fastapi import APIRouter, Request, Response

from ..errors import MethodNotAllowed, NoSuchBucket, NotImplementedOperation, S3Error, drain
from ..s3xml import element, iso8601, to_xml
from ..storage import StorageBackend

router = APIRouter()

MAX_KEYS = 1000

# Bucket sub-resources hadro recognises but does not implement.
_UNSUPPORTED = {
    "acl", "analytics", "cors", "encryption", "intelligent-tiering", "inventory",
    "lifecycle", "logging", "metrics", "notification", "object-lock", "ownershipControls",
    "policy", "policyStatus", "publicAccessBlock", "replication", "requestPayment",
    "tagging", "uploads", "versioning", "versions", "website", "accelerate",
}  # fmt: skip


def _backend(request: Request) -> StorageBackend:
    return request.app.state.backend


def _xml(root, status_code: int = 200) -> Response:
    return Response(to_xml(root), status_code=status_code, media_type="application/xml")


@router.get("/")
def list_buckets(request: Request) -> Response:
    """https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListBuckets.html"""
    root = element("ListAllMyBucketsResult")
    owner = element("Owner", parent=root)
    element("ID", "hadro", parent=owner)
    element("DisplayName", "hadro", parent=owner)
    buckets = element("Buckets", parent=root)
    for bucket in _backend(request).list_buckets():
        node = element("Bucket", parent=buckets)
        element("Name", bucket.name, parent=node)
        element("CreationDate", iso8601(bucket.created), parent=node)
    return _xml(root)


@router.head("/{bucket}")
@router.head("/{bucket}/")
def head_bucket(request: Request, bucket: str) -> Response:
    """https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadBucket.html"""
    if not _backend(request).bucket_exists(bucket):
        raise NoSuchBucket(bucket)
    return Response(headers={"x-amz-bucket-region": request.app.state.config.region})


@router.get("/{bucket}")
@router.get("/{bucket}/")
def get_bucket(request: Request, bucket: str) -> Response:
    params = request.query_params
    for name in params:
        if name in _UNSUPPORTED:
            raise NotImplementedOperation(f"The ?{name} bucket operation")
    if "location" in params:
        return get_bucket_location(request, bucket)
    if params.get("list-type") == "2":
        return list_objects(request, bucket, v2=True)
    return list_objects(request, bucket, v2=False)


@router.post("/{bucket}")
@router.post("/{bucket}/")
async def post_bucket(request: Request, bucket: str) -> Response:
    # e.g. DeleteObjects; hadro never writes.
    await drain(request)
    raise MethodNotAllowed()


def get_bucket_location(request: Request, bucket: str) -> Response:
    """https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetBucketLocation.html"""
    if not _backend(request).bucket_exists(bucket):
        raise NoSuchBucket(bucket)
    region = request.app.state.config.region
    # S3 reports us-east-1 as an empty LocationConstraint.
    return _xml(element("LocationConstraint", None if region == "us-east-1" else region))


def list_objects(request: Request, bucket: str, v2: bool) -> Response:
    """https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html
    https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjects.html"""
    params = request.query_params
    prefix = params.get("prefix", "")
    delimiter = params.get("delimiter", "")
    url_encode = params.get("encoding-type") == "url"
    try:
        max_keys = min(int(params.get("max-keys", MAX_KEYS)), MAX_KEYS)
    except ValueError:
        raise S3Error("InvalidArgument", "max-keys must be an integer.") from None
    if max_keys < 0:
        raise S3Error("InvalidArgument", "max-keys must not be negative.")

    if v2:
        token = params.get("continuation-token")
        start_after = params.get("start-after", "")
        pivot = _decode_token(token) if token else start_after
    else:
        marker = params.get("marker", "")
        pivot = marker

    def encode(value: str) -> str:
        return quote(value, safe="/") if url_encode else value

    contents, common_prefixes, truncated, last = [], [], False, None
    try:
        objects = _backend(request).list_objects(bucket, prefix)
        for obj in objects:
            key = obj.key
            if pivot and key <= pivot:
                continue
            common = None
            if delimiter:
                index = key.find(delimiter, len(prefix))
                if index >= 0:
                    common = key[: index + len(delimiter)]
                    if common == last or (pivot and common == pivot):
                        continue  # already reported this prefix
            if len(contents) + len(common_prefixes) >= max_keys:
                truncated = True
                break
            if common is not None:
                common_prefixes.append(common)
                last = common
            else:
                contents.append(obj)
                last = key
    except KeyError:
        raise NoSuchBucket(bucket) from None

    root = element("ListBucketResult")
    element("Name", bucket, parent=root)
    element("Prefix", encode(prefix), parent=root)
    if v2:
        if token:
            element("ContinuationToken", token, parent=root)
        if start_after:
            element("StartAfter", encode(start_after), parent=root)
        element("KeyCount", len(contents) + len(common_prefixes), parent=root)
    else:
        element("Marker", encode(marker), parent=root)
    element("MaxKeys", max_keys, parent=root)
    if delimiter:
        element("Delimiter", encode(delimiter), parent=root)
    if url_encode:
        element("EncodingType", "url", parent=root)
    element("IsTruncated", "true" if truncated else "false", parent=root)
    if truncated and last is not None:
        if v2:
            element("NextContinuationToken", _encode_token(last), parent=root)
        else:
            element("NextMarker", encode(last), parent=root)

    for obj in contents:
        node = element("Contents", parent=root)
        element("Key", encode(obj.key), parent=node)
        element("LastModified", iso8601(obj.last_modified), parent=node)
        element("ETag", obj.etag, parent=node)
        element("Size", obj.size, parent=node)
        element("StorageClass", "STANDARD", parent=node)
    for common in common_prefixes:
        node = element("CommonPrefixes", parent=root)
        element("Prefix", encode(common), parent=node)
    return _xml(root)


def _encode_token(key: str) -> str:
    return base64.urlsafe_b64encode(key.encode("utf-8")).decode("ascii")


def _decode_token(token: str) -> str | None:
    try:
        return base64.b64decode(token.encode("ascii"), altchars=b"-_", validate=True).decode()
    except (ValueError, UnicodeError):
        raise S3Error("InvalidArgument", "The continuation token provided is incorrect.") from None
