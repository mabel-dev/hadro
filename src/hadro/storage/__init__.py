from __future__ import annotations

from ..config import Config
from .base import BucketInfo, ObjectInfo, StorageBackend
from .cache import CachedBackend


def create_backend(config: Config) -> StorageBackend:
    if config.backend == "local":
        from .local import LocalBackend

        backend: StorageBackend = LocalBackend(config.data)
    elif config.backend == "gcs":
        from .gcs import GCSBackend

        backend = GCSBackend(config.gcs_project)
    else:
        raise ValueError(f"Unknown backend {config.backend!r}; expected 'local' or 'gcs'.")

    if config.cache_bytes > 0:
        backend = CachedBackend(backend, config.cache_bytes, config.cache_ttl)
    return backend


__all__ = ["BucketInfo", "CachedBackend", "ObjectInfo", "StorageBackend", "create_backend"]
