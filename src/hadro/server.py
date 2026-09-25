"""Run hadro in a background thread, e.g. from a test suite.

with hadro.Server(data="tests/data") as server:
    s3 = boto3.client("s3", endpoint_url=server.endpoint, ...)
"""

from __future__ import annotations

import socket
import threading
import time

from typing_extensions import Self

from .config import Config


class Server:
    def __init__(self, config: Config | None = None, backend=None, **settings):
        """``settings`` are ``Config`` fields, e.g. ``data="./data"`` or ``port=0``.

        The default port of 0 picks a free port; see ``endpoint``.
        """
        settings.setdefault("port", 0 if config is None else config.port)
        self.config = config or Config.from_env(**settings)
        if config is not None:
            for key, value in settings.items():
                setattr(self.config, key, value)
        self.backend = backend
        self._server = None
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None

    @property
    def endpoint(self) -> str:
        return f"http://{self.config.host}:{self.config.port}"

    def start(self, timeout: float = 10.0) -> Server:
        import uvicorn

        from .app import create_app

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((self.config.host, self.config.port))
        self.config.port = self._socket.getsockname()[1]

        app = create_app(self.config, self.backend)
        self._server = uvicorn.Server(
            uvicorn.Config(app, log_level="warning", access_log=False, lifespan="off")
        )
        self._thread = threading.Thread(
            target=self._server.run, kwargs={"sockets": [self._socket]}, daemon=True
        )
        self._thread.start()

        deadline = time.monotonic() + timeout
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() > deadline:
                self.stop()
                raise RuntimeError("hadro server failed to start")
            time.sleep(0.01)
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self._socket is not None:
            self._socket.close()
        self._server = self._thread = self._socket = None

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *exc_info) -> None:
        self.stop()
