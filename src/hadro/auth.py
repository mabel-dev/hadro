"""AWS Signature Version 4 verification.

Supports signatures in the ``Authorization`` header and presigned URLs
(``X-Amz-Signature`` in the query string). Only used when an access key and
secret key are configured; otherwise every request is allowed.

https://docs.aws.amazon.com/AmazonS3/latest/API/sig-v4-authenticating-requests.html
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, quote

from .errors import S3Error

ALGORITHM = "AWS4-HMAC-SHA256"
UNSIGNED = "UNSIGNED-PAYLOAD"


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret_key: str, date: str, region: str, service: str) -> bytes:
    key = _hmac(("AWS4" + secret_key).encode("utf-8"), date)
    for part in (region, service, "aws4_request"):
        key = _hmac(key, part)
    return key


def canonical_query(pairs: list[tuple[str, str]]) -> str:
    encoded = sorted((quote(k, safe="-_.~"), quote(v, safe="-_.~")) for k, v in pairs)
    return "&".join(f"{k}={v}" for k, v in encoded)


def _parse_credential(credential: str) -> tuple[str, str, str, str]:
    try:
        access_key, date, region, service, terminator = credential.split("/")
    except ValueError:
        raise S3Error("AuthorizationHeaderMalformed", "Malformed credential scope.") from None
    if terminator != "aws4_request":
        raise S3Error("AuthorizationHeaderMalformed", "Malformed credential scope.")
    return access_key, date, region, service


def _parse_authorization(header: str) -> dict[str, str]:
    if not header.startswith(ALGORITHM + " "):
        raise S3Error("InvalidRequest", "Only AWS4-HMAC-SHA256 (Signature Version 4) is supported.")
    fields = {}
    for part in header[len(ALGORITHM) + 1 :].split(","):
        name, _, value = part.strip().partition("=")
        fields[name] = value
    if not {"Credential", "SignedHeaders", "Signature"} <= fields.keys():
        raise S3Error("AuthorizationHeaderMalformed", "Malformed Authorization header.")
    return fields


def verify(
    *,
    method: str,
    raw_path: str,
    query_string: str,
    headers: Mapping[str, str],
    body: bytes,
    access_key: str,
    secret_key: str,
    now: datetime | None = None,
) -> None:
    """Raise ``S3Error`` unless the request carries a valid SigV4 signature.

    ``raw_path`` must be the path exactly as sent (still percent-encoded);
    ``headers`` must use lower-case names.
    """
    now = now or datetime.now(timezone.utc)
    query = parse_qsl(query_string, keep_blank_values=True)
    query_map = dict(query)

    if "X-Amz-Signature" in query_map:
        if query_map.get("X-Amz-Algorithm") != ALGORITHM:
            raise S3Error("AuthorizationQueryParametersError", "Unsupported X-Amz-Algorithm.")
        credential = query_map.get("X-Amz-Credential", "")
        signed_headers = query_map.get("X-Amz-SignedHeaders", "")
        signature = query_map["X-Amz-Signature"]
        amz_date = query_map.get("X-Amz-Date", "")
        payload_hash = UNSIGNED
        query = [(k, v) for k, v in query if k != "X-Amz-Signature"]
        try:
            expires = int(query_map.get("X-Amz-Expires", "0"))
            signed_at = datetime.strptime(amz_date, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            raise S3Error("AuthorizationQueryParametersError", "Malformed presigned URL.") from None
        if now > signed_at + timedelta(seconds=expires):
            raise S3Error("AccessDenied", "Request has expired", 403)
    elif "authorization" in headers:
        fields = _parse_authorization(headers["authorization"])
        credential = fields["Credential"]
        signed_headers = fields["SignedHeaders"]
        signature = fields["Signature"]
        amz_date = headers.get("x-amz-date", "")
        payload_hash = headers.get("x-amz-content-sha256", "")
        if not payload_hash:
            payload_hash = hashlib.sha256(body).hexdigest()
        elif (
            payload_hash != UNSIGNED
            and not payload_hash.startswith("STREAMING-")
            and hashlib.sha256(body).hexdigest() != payload_hash
        ):
            raise S3Error(
                "XAmzContentSHA256Mismatch",
                "The provided 'x-amz-content-sha256' header does not match the body.",
            )
    else:
        raise S3Error("AccessDenied", "Access Denied", 403)

    key_id, date, region, service = _parse_credential(credential)
    if key_id != access_key:
        raise S3Error(
            "InvalidAccessKeyId",
            "The AWS Access Key Id you provided does not exist in our records.",
            403,
        )

    header_names = [h for h in signed_headers.split(";") if h]
    canonical_headers = "".join(
        f"{name}:{' '.join(headers.get(name, '').split())}\n" for name in header_names
    )
    canonical_request = "\n".join(
        [
            method.upper(),
            raw_path or "/",
            canonical_query(query),
            canonical_headers,
            ";".join(header_names),
            payload_hash,
        ]
    )
    string_to_sign = "\n".join(
        [
            ALGORITHM,
            amz_date,
            f"{date}/{region}/{service}/aws4_request",
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    expected = hmac.new(
        signing_key(secret_key, date, region, service),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise S3Error(
            "SignatureDoesNotMatch",
            "The request signature we calculated does not match the signature you provided.",
            403,
        )
