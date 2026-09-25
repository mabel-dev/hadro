import shutil
from pathlib import Path

import boto3
import pytest
from botocore.config import Config as BotoConfig
from fastapi.testclient import TestClient

import hadro
from hadro.app import create_app

REPO_DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory) -> Path:
    """The repo's sample data plus a bucket with a nested layout for listing tests."""
    root = tmp_path_factory.mktemp("data")
    shutil.copytree(REPO_DATA, root, dirs_exist_ok=True)
    nested = root / "nested"
    for key in [
        "a.txt",
        "b/1.txt",
        "b/2.txt",
        "b/c/3.txt",
        "d/4.txt",
        "d/e/5.txt",
        "space and %25.txt",
        "z.csv",
    ]:
        path = nested / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"contents of {key}")
    (root / "empty").mkdir()
    (root / "secret.txt").write_text("outside any bucket")
    return root


@pytest.fixture(scope="session")
def client(data_dir) -> TestClient:
    return TestClient(create_app(hadro.Config(data=str(data_dir))))


@pytest.fixture(scope="session")
def server(data_dir):
    with hadro.Server(data=str(data_dir)) as running:
        yield running


def make_s3(endpoint: str, access_key: str = "anything", secret_key: str = "anything"):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="eu-west-2",
        config=BotoConfig(s3={"addressing_style": "path"}, retries={"max_attempts": 1}),
    )


@pytest.fixture(scope="session")
def s3(server):
    return make_s3(server.endpoint)
