"""Entry point: ``python -m visionstate``."""

import asyncio
import logging
import os
import signal
import sys
from types import FrameType

import uvicorn

from .main import create_app
from .redact import RedactingFormatter
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


STARTUP_FAILURE = 3  # same exit code as uvicorn.run


async def serve(server: Server) -> None:
    """Run the server, then end the process without waiting for AI worker threads.

    AI inference runs in worker threads that can not be interrupted, and asyncio waits for them
    (up to five minutes) when the event loop closes. On a slow device an object detection can
    take longer than Home Assistant's stop timeout, which would then kill the app and show
    *Error*. By the time ``serve`` returns the app has shut down cleanly (MQTT offline sent,
    database closed), so a result still being computed can safely be dropped.
    """
    await server.serve()
    logging.shutdown()
    os._exit(0 if server.started else STARTUP_FAILURE)


def main() -> None:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level.upper())
    for handler in logging.getLogger().handlers:
        handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    config = uvicorn.Config(
        create_app(settings),
        host="0.0.0.0",  # noqa: S104 - only reachable through Home Assistant Ingress
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )
    server = Server(config)
    # uvicorn's own loop choice, except on Windows (local development only): its Proactor loop
    # does not work with aiomqtt.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else config.get_loop_factory()
    asyncio.run(serve(server), loop_factory=loop_factory)


if __name__ == "__main__":
    main()
