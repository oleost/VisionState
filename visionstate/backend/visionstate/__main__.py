"""Entry point: ``python -m visionstate``."""

import asyncio
import logging
import signal
import sys
from types import FrameType

import uvicorn

from .main import create_app
from .settings import load_settings


class Server(uvicorn.Server):
    """uvicorn server that treats SIGTERM as a normal stop.

    Home Assistant stops apps with SIGTERM. uvicorn shuts down gracefully and then re-raises the
    signal, so the process ends "killed by SIGTERM" (exit code 143) and Home Assistant shows the
    app as *Error* instead of *Stopped*. After a graceful shutdown we exit with code 0 instead.
    """

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        super().handle_exit(sig, frame)
        captured = getattr(self, "_captured_signals", None)
        if sig == signal.SIGTERM and captured and sig in captured:
            captured.remove(sig)


def main() -> None:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    config = uvicorn.Config(
        create_app(settings),
        host="0.0.0.0",  # noqa: S104 - only reachable through Home Assistant Ingress
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )
    server = Server(config)
    if sys.platform == "win32":
        # Local development only: uvicorn picks a Proactor loop on Windows, which aiomqtt cannot use.
        asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
    else:
        server.run()


if __name__ == "__main__":
    main()
