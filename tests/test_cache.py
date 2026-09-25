from datetime import UTC, datetime

from hadro.storage import CachedBackend, ObjectInfo, StorageBackend


class CountingBackend(StorageBackend):
    def __init__(self, objects):
        self.objects = objects
        self.gets = []

    def head_object(self, bucket, key):
        content = self.objects.get(key)
        if content is None:
            return None
        return ObjectInfo(key, len(content), datetime.now(UTC), '"x"')

    def get_object(self, bucket, key, start=None, end=None):
        self.gets.append((key, start, end))
        content = self.objects.get(key)
        if content is None or start is None:
            return content
        return content[start : end + 1]


def test_hits_do_not_reach_backend():
    backend = CountingBackend({"a": b"0123456789"})
    cached = CachedBackend(backend, max_bytes=100)
    assert cached.get_object("b", "a") == b"0123456789"
    assert cached.get_object("b", "a") == b"0123456789"
    assert cached.get_object("b", "a", 2, 4) == b"234"
    assert backend.gets == [("a", None, None)]


def test_missing_objects_are_not_cached():
    backend = CountingBackend({})
    cached = CachedBackend(backend, max_bytes=100)
    assert cached.get_object("b", "a") is None
    backend.objects["a"] = b"now here"
    assert cached.get_object("b", "a") == b"now here"


def test_least_recently_used_is_evicted():
    backend = CountingBackend({k: b"x" * 20 for k in "abcdef"})
    cached = CachedBackend(backend, max_bytes=100)
    for key in "abcde":
        cached.get_object("b", key)
    cached.get_object("b", "a")  # a is now the most recently used
    cached.get_object("b", "f")  # evicts b
    backend.gets.clear()
    cached.get_object("b", "a")
    cached.get_object("b", "b")
    assert backend.gets == [("b", None, None)]
    assert cached._size <= 100


def test_large_objects_bypass_cache_for_ranges():
    backend = CountingBackend({"big": b"x" * 1000})
    cached = CachedBackend(backend, max_bytes=100)
    assert cached.get_object("b", "big", 0, 9) == b"x" * 10
    assert backend.gets == [("big", 0, 9)]
    assert cached._size == 0


def test_ttl_expiry(monkeypatch):
    import hadro.storage.cache as cache_module

    now = [1000.0]
    monkeypatch.setattr(cache_module.time, "monotonic", lambda: now[0])
    backend = CountingBackend({"a": b"v1"})
    cached = CachedBackend(backend, max_bytes=100, ttl=10)
    assert cached.get_object("b", "a") == b"v1"
    backend.objects["a"] = b"v2"
    assert cached.get_object("b", "a") == b"v1"
    now[0] += 11
    assert cached.get_object("b", "a") == b"v2"
