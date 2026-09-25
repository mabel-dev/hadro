import base64
import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import hadro
from hadro.cli import build_parser
from hadro.storage import CachedBackend, create_backend
from hadro.storage.local import LocalBackend


def test_env_and_overrides(monkeypatch):
    monkeypatch.setenv("HADRO_BACKEND", "GCS")
    monkeypatch.setenv("HADRO_PORT", "9000")
    monkeypatch.setenv("HADRO_ACCESS_KEY", "a")
    config = hadro.Config.from_env(port=9100)
    assert config.backend == "gcs" and config.port == 9100
    assert not config.auth_enabled  # needs both keys
    assert config.cache_bytes == 256 * 1024 * 1024


def test_cloud_run_port(monkeypatch):
    monkeypatch.setenv("PORT", "8081")
    assert hadro.Config.from_env().port == 8081


def test_backend_selection(tmp_path):
    assert isinstance(create_backend(hadro.Config(data=str(tmp_path))), LocalBackend)
    cached = create_backend(hadro.Config(data=str(tmp_path), cache_mb=1))
    assert isinstance(cached, CachedBackend)
    with pytest.raises(ValueError):
        create_backend(hadro.Config(backend="s3"))


def test_cli_parses_everything():
    args = build_parser().parse_args(["./somewhere", "--port", "1", "--backend", "gcs"])
    assert args.data == "./somewhere" and args.port == 1 and args.backend == "gcs"


def test_gcs_metadata_mapping():
    gcs = pytest.importorskip("hadro.storage.gcs")
    digest = hashlib.md5(b"hello").digest()
    blob = SimpleNamespace(
        name="a/b.parquet",
        size="5",
        updated=datetime(2026, 1, 1, tzinfo=timezone.utc),
        md5_hash=base64.b64encode(digest).decode(),
        etag="CJ",
        content_type=None,
    )
    info = gcs._info(blob)
    assert info.etag == f'"{digest.hex()}"' and info.size == 5
    composite = SimpleNamespace(**{**blob.__dict__, "md5_hash": None, "content_type": "x/y"})
    assert gcs._info(composite).etag == '"CJ"'
    assert gcs._info(composite).content_type == "x/y"
