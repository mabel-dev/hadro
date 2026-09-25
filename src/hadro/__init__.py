"""hadro - a small, read-only, S3-compatible server for local files and GCS."""

__version__ = "0.6.0"

from .config import Config
from .server import Server


def create_app(*args, **kwargs):
    from .app import create_app as _create_app

    return _create_app(*args, **kwargs)


__all__ = ["Config", "Server", "__version__", "create_app"]
