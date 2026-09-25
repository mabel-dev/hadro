"""In-memory read-through cache for slow (remote) backends."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Iterator

from .base import BucketInfo, ObjectInfo, StorageBackend


class CachedBackend(StorageBackend):
    """LRU cache of object bytes and metadata, bounded by total bytes.

    Entries expire after ``ttl`` seconds (0 keeps them until evicted), so changes
    in the underlying store show up eventually. Missing objects are never cached.
    Objects larger than a quarter of the cache bypass it.
    """

    def __init__(self, backend: StorageBackend, max_bytes: int, ttl: int = 300):
        self.backend = backend
        self.max_bytes = max_bytes
        self.max_item_bytes = max_bytes // 4
        self.ttl = ttl
        self._data: OrderedDict[tuple[str, str], tuple[float, bytes]] = OrderedDict()
        self._heads: OrderedDict[tuple[str, str], tuple[float, ObjectInfo]] = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    def _fresh(self, stored_at: float) -> bool:
        return self.ttl <= 0 or time.monotonic() - stored_at < self.ttl

    def _get_cached(self, key: tuple[str, str]) -> bytes | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if not self._fresh(entry[0]):
                self._size -= len(entry[1])
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return entry[1]

    def _store(self, key: tuple[str, str], content: bytes) -> None:
        with self._lock:
            if key in self._data:
                self._size -= len(self._data.pop(key)[1])
            self._data[key] = (time.monotonic(), content)
            self._size += len(content)
            while self._size > self.max_bytes and self._data:
                _, (_, evicted) = self._data.popitem(last=False)
                self._size -= len(evicted)

    def list_buckets(self) -> list[BucketInfo]:
        return self.backend.list_buckets()

    def bucket_exists(self, bucket: str) -> bool:
        return self.backend.bucket_exists(bucket)

    def list_objects(self, bucket: str, prefix: str = "") -> Iterator[ObjectInfo]:
        return self.backend.list_objects(bucket, prefix)

    def head_object(self, bucket: str, key: str) -> ObjectInfo | None:
        cache_key = (bucket, key)
        with self._lock:
            entry = self._heads.get(cache_key)
            if entry is not None and self._fresh(entry[0]):
                return entry[1]
        info = self.backend.head_object(bucket, key)
        if info is not None:
            with self._lock:
                self._heads[cache_key] = (time.monotonic(), info)
                while len(self._heads) > 10_000:
                    self._heads.popitem(last=False)
        return info

    def get_object(
        self, bucket: str, key: str, start: int | None = None, end: int | None = None
    ) -> bytes | None:
        cache_key = (bucket, key)
        content = self._get_cached(cache_key)
        if content is None:
            if start is not None:
                # Ranged reads of large objects (e.g. Parquet footers) go straight
                # through rather than pulling the whole object.
                info = self.head_object(bucket, key)
                if info is None:
                    return None
                if info.size > self.max_item_bytes:
                    return self.backend.get_object(bucket, key, start, end)
            content = self.backend.get_object(bucket, key)
            if content is None:
                return None
            if len(content) <= self.max_item_bytes:
                self._store(cache_key, content)
        if start is None:
            return content
        return content[start : end + 1]
