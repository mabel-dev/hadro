"""Command line entry point: ``hadro [DATA] [options]``."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import Config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hadro",
        description="Serve a directory or GCS project over a read-only, S3-compatible API.",
        epilog="Every option can also be set with a HADRO_<OPTION> environment variable.",
    )
    parser.add_argument(
        "data",
        nargs="?",
        help="directory to serve; each sub-directory is a bucket (local backend)",
    )
    parser.add_argument("--backend", choices=["local", "gcs"])
    parser.add_argument("--gcs-project", help="GCS project to list buckets from")
    parser.add_argument("--host", help="interface to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, help="port to listen on (default 8080)")
    parser.add_argument("--region", help="region reported to clients (default eu-west-2)")
    parser.add_argument(
        "--cache-mb", type=int, help="object cache size (default 256 for gcs, 0 for local)"
    )
    parser.add_argument("--cache-ttl", type=int, help="seconds before cached objects expire")
    parser.add_argument("--access-key", help="require SigV4 requests signed with this key")
    parser.add_argument("--secret-key", help="secret for --access-key")
    parser.add_argument("--log-level", choices=["critical", "error", "warning", "info", "debug"])
    parser.add_argument("--version", action="version", version=f"hadro {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = Config.from_env(
        data=args.data,
        backend=args.backend,
        gcs_project=args.gcs_project,
        host=args.host,
        port=args.port,
        region=args.region,
        cache_mb=args.cache_mb,
        cache_ttl=args.cache_ttl,
        access_key=args.access_key,
        secret_key=args.secret_key,
        log_level=args.log_level,
    )
    if bool(config.access_key) != bool(config.secret_key):
        print("hadro: --access-key and --secret-key must be given together", file=sys.stderr)
        return 2

    import uvicorn

    from .app import create_app

    try:
        app = create_app(config)
    except (ImportError, ValueError) as exc:
        print(f"hadro: {exc}", file=sys.stderr)
        return 2

    source = config.data if config.backend == "local" else f"gcs:{config.gcs_project or ''}"
    auth = "SigV4 required" if config.auth_enabled else "anonymous access"
    print(f"hadro {__version__} serving {source} ({auth})", file=sys.stderr)
    uvicorn.run(app, host=config.host, port=config.port, log_level=config.log_level)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
