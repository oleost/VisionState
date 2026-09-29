"""Entry point: ``python -m visionstate``."""

import asyncio
import logging
import sys

import uvicorn

from .main import create_app
from .settings import load_settings


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
    server = uvicorn.Server(config)
    if sys.platform == "win32":
        # Local development only: uvicorn picks a Proactor loop on Windows, which aiomqtt cannot use.
        asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
    else:
        server.run()


if __name__ == "__main__":
    main()
