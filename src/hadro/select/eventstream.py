"""Encoder for the AWS event stream framing used by SelectObjectContent.

https://docs.aws.amazon.com/AmazonS3/latest/API/RESTSelectObjectAppendix.html
"""

from __future__ import annotations

import struct
import zlib


def _encode_headers(headers: dict[str, str]) -> bytes:
    encoded = bytearray()
    for name, value in headers.items():
        name_bytes = name.encode("utf-8")
        value_bytes = value.encode("utf-8")
        encoded.append(len(name_bytes))
        encoded.extend(name_bytes)
        encoded.append(7)  # header value type: string
        encoded.extend(struct.pack(">H", len(value_bytes)))
        encoded.extend(value_bytes)
    return bytes(encoded)


def _message(headers: dict[str, str], payload: bytes = b"") -> bytes:
    header_bytes = _encode_headers(headers)
    total_length = 12 + len(header_bytes) + len(payload) + 4
    prelude = struct.pack(">II", total_length, len(header_bytes))
    prelude_crc = struct.pack(">I", zlib.crc32(prelude) & 0xFFFFFFFF)
    data = prelude + prelude_crc + header_bytes + payload
    return data + struct.pack(">I", zlib.crc32(data) & 0xFFFFFFFF)


def event(event_type: str, payload: bytes = b"", content_type: str | None = None) -> bytes:
    headers = {":message-type": "event", ":event-type": event_type}
    if content_type:
        headers[":content-type"] = content_type
    return _message(headers, payload)


def records(payload: bytes) -> bytes:
    return event("Records", payload, "application/octet-stream")


def stats(scanned: int, processed: int, returned: int) -> bytes:
    payload = (
        f'<?xml version="1.0" encoding="UTF-8"?><Stats><BytesScanned>{scanned}</BytesScanned>'
        f"<BytesProcessed>{processed}</BytesProcessed>"
        f"<BytesReturned>{returned}</BytesReturned></Stats>"
    ).encode()
    return event("Stats", payload, "text/xml")


def end() -> bytes:
    return event("End")


def error(code: str, message: str) -> bytes:
    return _message({":message-type": "error", ":error-code": code, ":error-message": message})
