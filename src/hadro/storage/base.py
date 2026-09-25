"""Storage backend interface.

Backends are read-only. Keys are returned in lexicographic (code point) order,
which is the same order S3 uses for UTF-8 keys.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class BucketInfo:
    name: str
    created: datetime


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int
    last_modified: datetime  # timezone-aware, UTC
    etag: str  # quoted, as it appears in S3 responses
    content_type: str = "application/octet-stream"


class StorageBackend:
    def list_buckets(self) -> list[BucketInfo]:
        raise NotImplementedError

    def bucket_exists(self, bucket: str) -> bool:
        raise NotImplementedError

    def list_objects(self, bucket: str, prefix: str = "") -> Iterator[ObjectInfo]:
        """Yield objects whose key starts with ``prefix``, sorted by key.

        Raises ``KeyError`` if the bucket does not exist.
        """
        raise NotImplementedError

    def head_object(self, bucket: str, key: str) -> ObjectInfo | None:
        raise NotImplementedError

    def get_object(
        self, bucket: str, key: str, start: int | None = None, end: int | None = None
    ) -> bytes | None:
        """Return object bytes, or the inclusive ``start``-``end`` byte range."""
        raise NotImplementedError
