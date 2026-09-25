"""S3-style error responses.

https://docs.aws.amazon.com/AmazonS3/latest/API/ErrorResponses.html
"""

from __future__ import annotations

import uuid

from fastapi import Request
from fastapi.responses import Response

from .s3xml import element, to_xml


class S3Error(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def NoSuchBucket(bucket: str) -> S3Error:
    return S3Error("NoSuchBucket", f"The specified bucket does not exist: {bucket}", 404)


def NoSuchKey(key: str) -> S3Error:
    return S3Error("NoSuchKey", f"The specified key does not exist: {key}", 404)


def MethodNotAllowed() -> S3Error:
    return S3Error("MethodNotAllowed", "hadro is read-only; this method is not allowed.", 405)


def NotImplementedOperation(operation: str) -> S3Error:
    return S3Error("NotImplemented", f"{operation} is not implemented by hadro.", 501)


async def drain(request: Request) -> None:
    """Consume an unwanted request body before replying with an error.

    Clients that send ``Expect: 100-continue`` (boto3 uploads do) otherwise send
    the body anyway, and it corrupts the next request on the same connection.
    """
    async for _ in request.stream():
        pass


async def s3_error_handler(request: Request, exc: S3Error) -> Response:
    request_id = uuid.uuid4().hex[:16].upper()
    headers = {"x-amz-request-id": request_id}
    if request.method == "HEAD":
        # HEAD responses carry no body; clients rely on the status code alone.
        return Response(status_code=exc.status_code, headers=headers)
    root = element("Error")
    element("Code", exc.code, parent=root)
    element("Message", exc.message, parent=root)
    element("Resource", request.url.path, parent=root)
    element("RequestId", request_id, parent=root)
    return Response(
        to_xml(root, namespace=False),
        status_code=exc.status_code,
        media_type="application/xml",
        headers=headers,
    )
