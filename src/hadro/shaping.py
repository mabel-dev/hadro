"""Network shaping, so a local hadro behaves like a remote blob store.

Off by default. Every setting is zero-means-off:

* ``latency_ms`` (+ ``latency_jitter_ms``): delay before each request is served,
  the round trip to a remote store.
* ``bandwidth_mbps``: cap on each response's transfer rate, like a per-stream
  object-store limit. N concurrent responses move N times this in total.
* ``total_bandwidth_mbps``: cap on every response together, like a saturated
  client link. Concurrent responses share it.
* ``error_rate``: chance a request is answered ``503 SlowDown`` before any body.
  Deterministic per (``fault_seed``, request number) so runs are reproducible.

Implemented as a pure ASGI middleware. ``/health`` is never shaped.
"""

from __future__ import annotations

import asyncio
import random
import uuid

from .config import Config
from .s3xml import element, to_xml

CHUNK_SIZE = 64 * 1024


def validate(config: Config) -> None:
    for name in ("latency_ms", "latency_jitter_ms", "bandwidth_mbps", "total_bandwidth_mbps"):
        if getattr(config, name) < 0:
            raise ValueError(f"{name} must not be negative")
    if not 0.0 <= config.error_rate <= 1.0:
        raise ValueError("error_rate must be between 0 and 1")


class Shaper:
    def __init__(self, app, config: Config):
        validate(config)
        self.app = app
        self.latency_s = config.latency_ms / 1000.0
        self.jitter_s = config.latency_jitter_ms / 1000.0
        # bytes/sec; 0 = unlimited
        self.stream_bps = config.bandwidth_mbps * 1_000_000 / 8.0
        self.total_bps = config.total_bandwidth_mbps * 1_000_000 / 8.0
        self.error_rate = config.error_rate
        self.seed = config.fault_seed
        self._requests = 0
        # When the shared link is next free; only ever touched from the event loop.
        self._link_free_at = 0.0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] == "/health":
            await self.app(scope, receive, send)
            return

        self._requests += 1
        rng = random.Random((self.seed * 1_000_003) ^ (self._requests * 2_654_435_761))

        delay = self.latency_s + (rng.random() * self.jitter_s if self.jitter_s else 0.0)
        if delay:
            await asyncio.sleep(delay)

        if self.error_rate and rng.random() < self.error_rate:
            await self._slow_down(scope, send)
            return

        if not (self.stream_bps or self.total_bps):
            await self.app(scope, receive, send)
            return

        stream_free_at = 0.0

        async def paced_send(message):
            nonlocal stream_free_at
            body = message.get("body", b"") if message["type"] == "http.response.body" else b""
            if len(body) == 0:
                await send(message)
                return
            more = message.get("more_body", False)
            loop = asyncio.get_running_loop()
            for start in range(0, len(body), CHUNK_SIZE):
                chunk = body[start : start + CHUNK_SIZE]
                now = loop.time()
                ready = now
                if self.stream_bps:
                    stream_free_at = max(now, stream_free_at) + len(chunk) / self.stream_bps
                    ready = stream_free_at
                if self.total_bps:
                    self._link_free_at = max(now, self._link_free_at) + len(chunk) / self.total_bps
                    ready = max(ready, self._link_free_at)
                if ready > now:
                    await asyncio.sleep(ready - now)
                last = start + CHUNK_SIZE >= len(body)
                await send(
                    {"type": "http.response.body", "body": chunk, "more_body": more or not last}
                )

        await self.app(scope, receive, paced_send)

    @staticmethod
    async def _slow_down(scope, send):
        request_id = uuid.uuid4().hex[:16].upper()
        headers = [(b"x-amz-request-id", request_id.encode())]
        body = b""
        if scope["method"] != "HEAD":
            root = element("Error")
            element("Code", "SlowDown", parent=root)
            element("Message", "Please reduce your request rate.", parent=root)
            element("Resource", scope["path"], parent=root)
            element("RequestId", request_id, parent=root)
            body = to_xml(root, namespace=False)
            if isinstance(body, str):
                body = body.encode()
            headers += [
                (b"content-type", b"application/xml"),
                (b"content-length", str(len(body)).encode()),
            ]
        await send({"type": "http.response.start", "status": 503, "headers": headers})
        await send({"type": "http.response.body", "body": body})
