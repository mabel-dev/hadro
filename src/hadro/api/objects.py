"""Object-level operations: GetObject, HeadObject and SelectObjectContent."""

from __future__ import annotations

import re
from email.utils import format_datetime

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from .. import select
from ..errors import (
    MethodNotAllowed,
    NoSuchBucket,
    NoSuchKey,
    NotImplementedOperation,
    S3Error,
    drain,
)
from ..storage import ObjectInfo, StorageBackend

router = APIRouter()

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_UNSUPPORTED = {"acl", "attributes", "legal-hold", "retention", "tagging", "torrent", "uploadId"}


def _backend(request: Request) -> StorageBackend:
    return request.app.state.backend


def _headers(info: ObjectInfo) -> dict:
    return {
        "ETag": info.etag,
        "Last-Modified": format_datetime(info.last_modified, usegmt=True),
        "Accept-Ranges": "bytes",
        "Content-Type": info.content_type,
    }


def _parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Return an inclusive (start, end) range, or None to send the whole object."""
    if not header:
        return None
    match = _RANGE.match(header.strip())
    if match is None:
        return None  # like S3, ignore malformed and multi-range headers
    first, last = match.groups()
    if not first and not last:
        return None
    if not first:  # suffix range: the last N bytes
        length = int(last)
        if length == 0:
            raise _invalid_range(size)
        return max(size - length, 0), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or (last and int(last) < start):
        raise _invalid_range(size)
    return start, end


def _invalid_range(size: int) -> S3Error:
    return S3Error("InvalidRange", f"The requested range is not satisfiable (size {size}).", 416)


def _lookup(request: Request, bucket: str, key: str) -> ObjectInfo:
    backend = _backend(request)
    info = backend.head_object(bucket, key)
    if info is None:
        if not backend.bucket_exists(bucket):
            raise NoSuchBucket(bucket)
        raise NoSuchKey(key)
    return info


def _check_supported(request: Request) -> None:
    for name in request.query_params:
        if name in _UNSUPPORTED:
            raise NotImplementedOperation(f"The ?{name} object operation")


@router.head("/{bucket}/{key:path}")
def head_object(request: Request, bucket: str, key: str) -> Response:
    """https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html"""
    _check_supported(request)
    info = _lookup(request, bucket, key)
    headers = _headers(info)
    byte_range = _parse_range(request.headers.get("range"), info.size)
    if byte_range is None:
        headers["Content-Length"] = str(info.size)
        return Response(headers=headers)
    start, end = byte_range
    headers["Content-Length"] = str(end - start + 1)
    headers["Content-Range"] = f"bytes {start}-{end}/{info.size}"
    return Response(status_code=206, headers=headers)


@router.get("/{bucket}/{key:path}")
def get_object(request: Request, bucket: str, key: str) -> Response:
    """https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html"""
    _check_supported(request)
    info = _lookup(request, bucket, key)
    headers = _headers(info)
    media_type = headers.pop("Content-Type")
    byte_range = _parse_range(request.headers.get("range"), info.size)
    if byte_range is None:
        content = _backend(request).get_object(bucket, key)
        if content is None:  # removed since the HEAD
            raise NoSuchKey(key)
        return Response(content, headers=headers, media_type=media_type)

    start, end = byte_range
    content = _backend(request).get_object(bucket, key, start, end)
    if content is None:
        raise NoSuchKey(key)
    headers["Content-Range"] = f"bytes {start}-{start + len(content) - 1}/{info.size}"
    return Response(content, status_code=206, headers=headers, media_type=media_type)


@router.post("/{bucket}/{key:path}")
async def post_object(request: Request, bucket: str, key: str) -> Response:
    """https://docs.aws.amazon.com/AmazonS3/latest/API/API_SelectObjectContent.html"""
    if "select" not in request.query_params:
        await drain(request)
        raise MethodNotAllowed()

    body = await request.body()
    expression, output = select.parse_request(body)

    def run():
        backend = _backend(request)
        content = backend.get_object(bucket, key)
        if content is None:
            if not backend.bucket_exists(bucket):
                raise NoSuchBucket(bucket)
            raise NoSuchKey(key)
        return select.execute(content, expression, output)

    events = await run_in_threadpool(run)
    return StreamingResponse(events, media_type="application/octet-stream")
