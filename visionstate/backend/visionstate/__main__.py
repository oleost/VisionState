"""Entry point: ``python -m visionstate``."""

import asyncio
import logging
import sys

import uvicorn

from .main import create_app
from .settings import load_settings


def main() -> None:
    if sys.platform == "win32":  # local development: aiomqtt needs a selector event loop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    settings = load_settings()
    level = settings.log_level.upper()
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    uvicorn.run(
        create_app(settings), host="0.0.0.0", port=settings.port, log_level=settings.log_level.lower(), access_log=False
    )  # noqa: S104


if __name__ == "__main__":
    main()
