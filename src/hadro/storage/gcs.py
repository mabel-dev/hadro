"""Serve Google Cloud Storage buckets. Requires the ``gcs`` extra."""

from __future__ import annotations

import base64
import mimetypes
import os
from collections.abc import Iterator
from datetime import datetime, timezone

from .base import BucketInfo, ObjectInfo, StorageBackend

try:
    from google.api_core import exceptions as gcs_errors
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import storage
except ImportError as exc:  # pragma: no cover - depends on the environment
    raise ImportError(
        "The GCS backend needs google-cloud-storage: pip install 'hadro[gcs]'"
    ) from exc


def _info(blob) -> ObjectInfo:
    if blob.md5_hash:
        etag = base64.b64decode(blob.md5_hash).hex()
    else:  # composite objects have no MD5
        etag = (blob.etag or "").strip('"')
    return ObjectInfo(
        key=blob.name,
        size=int(blob.size or 0),
        last_modified=blob.updated or datetime.now(timezone.utc),
        etag=f'"{etag}"',
        content_type=blob.content_type
        or mimetypes.guess_type(blob.name)[0]
        or "application/octet-stream",
    )


class GCSBackend(StorageBackend):
    def __init__(self, project: str | None = None):
        self.project = project
        self._client = None

    @property
    def client(self):
        if self._client is None:
            if os.environ.get("STORAGE_EMULATOR_HOST"):
                self._client = storage.Client(
                    credentials=AnonymousCredentials(), project=self.project or "test"
                )
            else:  # pragma: no cover - needs real credentials
                self._client = storage.Client(project=self.project)
        return self._client

    def list_buckets(self) -> list[BucketInfo]:
        return [
            BucketInfo(b.name, b.time_created or datetime.now(timezone.utc))
            for b in self.client.list_buckets()
        ]

    def bucket_exists(self, bucket: str) -> bool:
        try:
            return self.client.lookup_bucket(bucket) is not None
        except gcs_errors.Forbidden:
            # Object readers often lack storage.buckets.get; assume it exists and
            # let object requests report the real error.
            return True

    def list_objects(self, bucket: str, prefix: str = "") -> Iterator[ObjectInfo]:
        try:
            for blob in self.client.list_blobs(bucket, prefix=prefix or None):
                yield _info(blob)
        except gcs_errors.NotFound:
            raise KeyError(bucket) from None

    def head_object(self, bucket: str, key: str) -> ObjectInfo | None:
        try:
            blob = self.client.bucket(bucket).get_blob(key)
        except gcs_errors.NotFound:
            return None
        return None if blob is None else _info(blob)

    def get_object(
        self, bucket: str, key: str, start: int | None = None, end: int | None = None
    ) -> bytes | None:
        blob = self.client.bucket(bucket).blob(key)
        try:
            # GCS ranges, like S3 ranges, include the end byte.
            return blob.download_as_bytes(start=start, end=end, checksum=None)
        except gcs_errors.NotFound:
            return None
