import threading
import time

import pytest
from fastapi.testclient import TestClient

import hadro
from hadro.app import create_app
from hadro.cli import build_parser

SIZE = 128 * 1024


@pytest.fixture()
def data(tmp_path):
    bucket = tmp_path / "b"
    bucket.mkdir()
    (bucket / "blob.bin").write_bytes(bytes(range(256)) * (SIZE // 256))
    return str(tmp_path)


def client(data, **shaping) -> TestClient:
    return TestClient(create_app(hadro.Config(data=data, **shaping)))


def timed(fn):
    start = time.monotonic()
    result = fn()
    return result, time.monotonic() - start


def test_off_by_default(data):
    assert not hadro.Config(data=data).shaping_enabled
    _, elapsed = timed(lambda: client(data).get("/b/blob.bin"))
    assert elapsed < 0.2


def test_latency_delays_every_request(data):
    c = client(data, latency_ms=200)
    response, elapsed = timed(lambda: c.get("/b/blob.bin"))
    assert response.status_code == 200 and elapsed >= 0.2
    _, elapsed = timed(lambda: c.head("/b/blob.bin"))
    assert elapsed >= 0.2


def test_health_is_never_shaped(data):
    _, elapsed = timed(lambda: client(data, latency_ms=500, error_rate=1.0).get("/health"))
    assert elapsed < 0.3


def test_per_response_bandwidth_caps_transfer_and_keeps_bytes(data):
    # 2 Mbps = 250 KB/s, so 128 KiB takes ~0.5s.
    c = client(data, bandwidth_mbps=2)
    response, elapsed = timed(lambda: c.get("/b/blob.bin"))
    assert elapsed >= 0.4
    assert response.content == bytes(range(256)) * (SIZE // 256)


def test_range_reads_are_shaped_and_correct(data):
    c = client(data, bandwidth_mbps=2)
    response, elapsed = timed(lambda: c.get("/b/blob.bin", headers={"Range": "bytes=10-70009"}))
    assert response.status_code == 206
    assert response.content == (bytes(range(256)) * (SIZE // 256))[10:70010]
    assert elapsed >= 0.2  # 70000 B at 250 KB/s


def test_total_bandwidth_is_shared_between_concurrent_responses(data):
    # Each response alone is unlimited; together they may move 250 KB/s, so two
    # 128 KiB reads take ~1s. Uncapped they would finish almost instantly.
    c = client(data, total_bandwidth_mbps=2)
    results = []

    def fetch():
        results.append(len(c.get("/b/blob.bin").content))

    start = time.monotonic()
    threads = [threading.Thread(target=fetch) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [SIZE, SIZE]
    assert time.monotonic() - start >= 0.9


def test_error_rate_one_is_503_slowdown(data):
    c = client(data, error_rate=1.0)
    response = c.get("/b/blob.bin")
    assert response.status_code == 503 and b"<Code>SlowDown</Code>" in response.content
    head = c.head("/b/blob.bin")
    assert head.status_code == 503 and head.content == b""


def test_faults_are_reproducible_for_a_seed(data):
    def statuses(seed):
        c = client(data, error_rate=0.5, fault_seed=seed)
        return [c.head("/b/blob.bin").status_code for _ in range(20)]

    first = statuses(7)
    assert first == statuses(7)
    assert set(first) == {200, 503}


@pytest.mark.parametrize(
    "bad", [{"latency_ms": -1}, {"bandwidth_mbps": -1}, {"error_rate": 1.5}, {"error_rate": -0.1}]
)
def test_invalid_settings_are_rejected(data, bad):
    with pytest.raises(ValueError):
        create_app(hadro.Config(data=data, **bad))


def test_env_and_cli(monkeypatch):
    monkeypatch.setenv("HADRO_LATENCY_MS", "40")
    monkeypatch.setenv("HADRO_TOTAL_BANDWIDTH_MBPS", "100")
    config = hadro.Config.from_env(error_rate=0.1)
    assert config.latency_ms == 40 and config.total_bandwidth_mbps == 100
    assert config.error_rate == 0.1 and config.shaping_enabled
    args = build_parser().parse_args(["--latency-ms", "5", "--bandwidth-mbps", "3", "--fault-seed", "9"])
    assert (args.latency_ms, args.bandwidth_mbps, args.fault_seed) == (5, 3, 9)
