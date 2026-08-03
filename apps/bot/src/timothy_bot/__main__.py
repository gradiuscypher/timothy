"""Entry point: `timothy-bot`.

Wait for the backend, then hold a gateway connection until something stops the process.
The order matters: a bot that reached Discord before it could reach the backend would
show a moderator a live command surface that answers every invocation with an error.
"""

import asyncio
import logging

import httpx

import timothy_logs as logs
from timothy_bot import api
from timothy_bot.client import TimothyBot
from timothy_bot.settings import Settings

logger = logging.getLogger("timothy.bot")


async def check_backend(client: httpx.AsyncClient) -> None:
    """Confirm the backend is answering before opening the gateway."""
    response = await client.get("/health")
    response.raise_for_status()
    logger.info("backend reachable: %s", response.json())


async def run(settings: Settings) -> None:
    """Connect to the backend, then to Discord."""
    async with api.create_client(
        base_url=settings.api_base_url,
        token=settings.internal_token.get_secret_value(),
        timeout=settings.request_timeout,
    ) as client:
        await check_backend(client)

        if not settings.gateway_enabled:
            logger.warning("gateway disabled: TIMOTHY_GATEWAY_ENABLED is off")
            await asyncio.Event().wait()  # pragma: no cover — the container idles here
        else:
            bot = TimothyBot(api.Api(client, actor=api.SYSTEM), settings)
            async with bot:
                await bot.start(settings.discord_token.get_secret_value())


def main() -> None:
    """Configure logging and run."""
    settings = Settings()
    logs.configure(
        "bot",
        level=settings.log_level,
        log_dir=settings.log_dir,
        # discord.py puts the token in the `Authorization` header it builds, and reports
        # a failed login by showing the request it made. The internal token travels the
        # same way to the backend.
        secrets=(
            settings.discord_token.get_secret_value(),
            settings.internal_token.get_secret_value(),
        ),
    )
    # httpx logs a line per request at INFO, and the relay already logs one per event
    # saying what the backend decided. Two lines for every gateway event is one too many.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
