"""Entry point: `timothy-bot`.

Phase 0 stands the process up and proves it can reach the backend. The gateway client,
the event relay and the slash commands arrive in phase 4.
"""

import asyncio
import logging

import httpx

from timothy_bot.settings import Settings

logger = logging.getLogger("timothy.bot")


async def run(settings: Settings) -> None:
    """Wait for the backend, then idle."""
    async with httpx.AsyncClient(base_url=settings.api_base_url) as client:
        response = await client.get("/health")
        response.raise_for_status()
        logger.info("backend reachable: %s", response.json())

    logger.info("no gateway client yet — phase 4")
    await asyncio.Event().wait()  # pragma: no cover — the container idles here


def main() -> None:
    """Configure logging and run."""
    settings = Settings()
    logging.basicConfig(level=settings.log_level)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
