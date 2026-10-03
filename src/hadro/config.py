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

    # Serve HTTPS when both are set (PEM files).
    tls_cert: str | None = None
    tls_key: str | None = None

    # Network shaping (see hadro.shaping); 0 disables each.
    latency_ms: float = 0.0
    latency_jitter_ms: float = 0.0
    bandwidth_mbps: float = 0.0
    total_bandwidth_mbps: float = 0.0
    error_rate: float = 0.0
    fault_seed: int = 0
    # Track peak concurrent responses and bytes in flight; read at /_shaping/stats.
    stats: bool = False

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
            tls_cert=_env("TLS_CERT"),
            tls_key=_env("TLS_KEY"),
            latency_ms=float(_env("LATENCY_MS", "0")),
            latency_jitter_ms=float(_env("LATENCY_JITTER_MS", "0")),
            bandwidth_mbps=float(_env("BANDWIDTH_MBPS", "0")),
            total_bandwidth_mbps=float(_env("TOTAL_BANDWIDTH_MBPS", "0")),
            error_rate=float(_env("ERROR_RATE", "0")),
            fault_seed=int(_env("FAULT_SEED", "0")),
            stats=_env("STATS", "0").lower() in ("1", "true", "yes"),
        )
        for key, value in overrides.items():
            if value is not None:
                setattr(config, key, value)
        return config

    @property
    def auth_enabled(self) -> bool:
        return bool(self.access_key and self.secret_key)

    @property
    def tls_enabled(self) -> bool:
        return bool(self.tls_cert and self.tls_key)

    @property
    def shaping_enabled(self) -> bool:
        return bool(
            self.latency_ms
            or self.latency_jitter_ms
            or self.bandwidth_mbps
            or self.total_bandwidth_mbps
            or self.error_rate
            or self.stats
        )

    @property
    def cache_bytes(self) -> int:
        cache_mb = self.cache_mb
        if cache_mb is None:
            cache_mb = 256 if self.backend == "gcs" else 0
        return cache_mb * 1024 * 1024
