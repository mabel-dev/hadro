"""Runtime configuration.

Every setting can be supplied as a keyword argument, a CLI flag, or a
``HADRO_*`` environment variable (in that order of precedence).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(f"HADRO_{name}")
    return default if value in (None, "") else value


@dataclass
class Config:
    # Storage
    backend: str = "local"
    data: str = "data"
    gcs_project: str | None = None
    # Size of the in-memory object cache; None picks a default per backend
    # (256MB for GCS, disabled for local files, which the OS already caches).
    cache_mb: int | None = None
    cache_ttl: int = 300

    # Server
    host: str = "127.0.0.1"
    port: int = 8080
    log_level: str = "info"
    region: str = "eu-west-2"

    # Signature checking is enabled only when both keys are set.
    access_key: str | None = None
    secret_key: str | None = None

    @classmethod
    def from_env(cls, **overrides) -> Config:
        cache_mb = _env("CACHE_MB")
        config = cls(
            backend=_env("BACKEND", "local").lower(),
            data=_env("DATA", "data"),
            gcs_project=_env("GCS_PROJECT"),
            cache_mb=int(cache_mb) if cache_mb is not None else None,
            cache_ttl=int(_env("CACHE_TTL", "300")),
            host=_env("HOST", "127.0.0.1"),
            port=int(_env("PORT", os.environ.get("PORT", "8080"))),
            log_level=_env("LOG_LEVEL", "info"),
            region=_env("REGION", "eu-west-2"),
            access_key=_env("ACCESS_KEY"),
            secret_key=_env("SECRET_KEY"),
        )
        for key, value in overrides.items():
            if value is not None:
                setattr(config, key, value)
        return config

    @property
    def auth_enabled(self) -> bool:
        return bool(self.access_key and self.secret_key)

    @property
    def cache_bytes(self) -> int:
        cache_mb = self.cache_mb
        if cache_mb is None:
            cache_mb = 256 if self.backend == "gcs" else 0
        return cache_mb * 1024 * 1024
