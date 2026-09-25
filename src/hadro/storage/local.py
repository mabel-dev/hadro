"""Serve a local directory: each sub-directory is a bucket, each file an object."""

from __future__ import annotations

import hashlib
import mimetypes
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from .base import BucketInfo, ObjectInfo, StorageBackend


def _content_type(key: str) -> str:
    return mimetypes.guess_type(key)[0] or "application/octet-stream"


class LocalBackend(StorageBackend):
    def __init__(self, root: str):
        self.root = Path(root).resolve()

    def _bucket_path(self, bucket: str) -> Path | None:
        if not bucket or bucket in (".", "..") or "/" in bucket or "\\" in bucket:
            return None
        path = self.root / bucket
        return path if path.is_dir() else None

    def _object_path(self, bucket: str, key: str) -> Path | None:
        bucket_path = self._bucket_path(bucket)
        if bucket_path is None or not key:
            return None
        path = (bucket_path / key).resolve()
        # Refuse anything that escapes the bucket (``..``, absolute keys, symlinks).
        if not path.is_relative_to(bucket_path.resolve()) or not path.is_file():
            return None
        return path

    @staticmethod
    def _info(key: str, stat: os.stat_result) -> ObjectInfo:
        # Hashing every file on each listing is too slow for large directories, so the
        # ETag is derived from size and mtime: stable across restarts and it changes
        # whenever the file does.
        digest = hashlib.md5(f"{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()
        return ObjectInfo(
            key=key,
            size=stat.st_size,
            last_modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            etag=f'"{digest}"',
            content_type=_content_type(key),
        )

    def list_buckets(self) -> list[BucketInfo]:
        if not self.root.is_dir():
            return []
        return [
            BucketInfo(entry.name, datetime.fromtimestamp(entry.stat().st_ctime, tz=UTC))
            for entry in sorted(self.root.iterdir(), key=lambda p: p.name)
            if entry.is_dir() and not entry.name.startswith(".")
        ]

    def bucket_exists(self, bucket: str) -> bool:
        return self._bucket_path(bucket) is not None

    def list_objects(self, bucket: str, prefix: str = "") -> Iterator[ObjectInfo]:
        bucket_path = self._bucket_path(bucket)
        if bucket_path is None:
            raise KeyError(bucket)

        # Only walk the deepest directory the prefix can be in.
        start = bucket_path
        directory = prefix.rpartition("/")[0]
        if directory:
            start = (bucket_path / directory).resolve()
            if not start.is_relative_to(bucket_path.resolve()) or not start.is_dir():
                return

        keys = []
        for dirpath, _, filenames in os.walk(start):
            relative = Path(dirpath).relative_to(bucket_path).as_posix()
            base = "" if relative == "." else relative + "/"
            keys.extend(base + name for name in filenames if (base + name).startswith(prefix))

        for key in sorted(keys):
            try:
                yield self._info(key, (bucket_path / key).stat())
            except FileNotFoundError:  # removed while listing
                continue

    def head_object(self, bucket: str, key: str) -> ObjectInfo | None:
        path = self._object_path(bucket, key)
        return None if path is None else self._info(key, path.stat())

    def get_object(
        self, bucket: str, key: str, start: int | None = None, end: int | None = None
    ) -> bytes | None:
        path = self._object_path(bucket, key)
        if path is None:
            return None
        with path.open("rb") as handle:
            if start is None:
                return handle.read()
            handle.seek(start)
            return handle.read(end - start + 1)
